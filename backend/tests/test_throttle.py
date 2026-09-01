"""Login throttling. Argon2 is deliberately expensive, so an unthrottled login
is a denial of service as well as a credential-stuffing risk."""


from app.config import get_settings
from app.services.security.throttle import Throttle


class FakeRedis:
    def __init__(self, broken=False):
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.broken = broken

    async def incr(self, key):
        if self.broken:
            raise ConnectionError("redis is down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        self.expiries[key] = seconds

    async def delete(self, key):
        self.counts.pop(key, None)


async def test_attempts_under_the_limit_are_allowed():
    throttle = Throttle(FakeRedis(), limit=3, window_seconds=60)
    for expected in (1, 2, 3):
        allowed, count = await throttle.hit("k")
        assert allowed is True and count == expected


async def test_the_limit_is_enforced():
    throttle = Throttle(FakeRedis(), limit=3, window_seconds=60)
    for _ in range(3):
        await throttle.hit("k")
    allowed, count = await throttle.hit("k")
    assert allowed is False and count == 4


async def test_a_window_is_set_only_on_the_first_hit():
    """Re-setting the TTL on every attempt would let an attacker extend it."""
    redis = FakeRedis()
    throttle = Throttle(redis, limit=5, window_seconds=900)
    await throttle.hit("k")
    await throttle.hit("k")
    assert redis.expiries == {"throttle:k": 900}


async def test_keys_are_independent():
    throttle = Throttle(FakeRedis(), limit=1, window_seconds=60)
    assert (await throttle.hit("a"))[0] is True
    assert (await throttle.hit("b"))[0] is True


async def test_a_successful_login_resets_the_counter():
    throttle = Throttle(FakeRedis(), limit=2, window_seconds=60)
    await throttle.hit("k")
    await throttle.reset("k")
    assert (await throttle.hit("k")) == (True, 1)


async def test_it_fails_open_when_redis_is_down():
    """Locking every operator out of their own dashboard is worse than the risk."""
    throttle = Throttle(FakeRedis(broken=True), limit=1, window_seconds=60)
    assert (await throttle.hit("k")) == (True, 0)


# --- the endpoint -----------------------------------------------------------


async def test_repeated_bad_logins_are_eventually_refused(api, admin, redis):
    limit = get_settings().login_attempt_limit
    for _ in range(limit):
        response = await api.post(
            "/api/auth/login", json={"email": admin["email"], "password": "wrong"}
        )
        assert response.status_code == 401

    blocked = await api.post(
        "/api/auth/login", json={"email": admin["email"], "password": "wrong"}
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"


async def test_throttling_applies_before_the_password_is_checked(api, admin, redis):
    """Even the correct password is refused once the limit is hit, so the
    expensive Argon2 verify never runs for a hammering attacker."""
    from tests.conftest import TEST_PASSWORD

    limit = get_settings().login_attempt_limit
    for _ in range(limit):
        await api.post("/api/auth/login", json={"email": admin["email"], "password": "wrong"})

    response = await api.post(
        "/api/auth/login", json={"email": admin["email"], "password": TEST_PASSWORD}
    )
    assert response.status_code == 429


async def test_a_successful_login_clears_the_counter(api, admin, redis):
    from tests.conftest import TEST_PASSWORD

    await api.post("/api/auth/login", json={"email": admin["email"], "password": "wrong"})
    await api.post("/api/auth/login", json={"email": admin["email"], "password": TEST_PASSWORD})
    assert redis.counts == {}
