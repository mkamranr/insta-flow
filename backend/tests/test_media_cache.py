"""The thumbnail cache on disk, and how a Meta CDN URL is read.

Both halves are security-shaped rather than cosmetic. The storage key is built
from ids that arrive from Meta, so path handling is load-bearing the same way
`test_attachments.py`'s is; and the fetch happens from a process sitting on the
compose network next to `mongodb:27017`, so the host allowlist is the only thing
stopping a tampered row pointing the fetcher at a neighbour.
"""

from datetime import UTC, datetime

import pytest

from app.services import media_cache
from app.services.media_cache import MediaCacheError, delete, digest_for, path_for, store
from app.services.meta import cdn

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 32

LIVE_URL = "https://instagram.ffjr1-2.fna.fbcdn.net/v/t51/123.jpg?oe=68D6F0AA&_nc_gid=x"


@pytest.fixture
def root(tmp_path):
    return tmp_path


# --- keys and paths ---------------------------------------------------------


def test_the_key_includes_the_account_so_two_workspaces_cannot_collide():
    """The same media id under two accounts must not name the same file.

    Media ids come from Meta and are not workspace-scoped; if the account were
    not in the hash, one tenant's resync would overwrite another's picture.
    """
    assert digest_for("acct-a", "17841400") != digest_for("acct-b", "17841400")
    assert digest_for("acct-a", "17841400") == digest_for("acct-a", "17841400")


def test_the_key_is_sharded_hex_and_nothing_else():
    key = media_cache.storage_key(digest_for("a", "1"), "image/jpeg")
    assert media_cache.KEY_PATTERN.match(key), key
    assert key.startswith(key[:2] + "/")


def test_a_crafted_media_id_cannot_shape_a_path():
    """The id is hashed, so `../` in a media id is just more bytes to digest."""
    key = media_cache.storage_key(digest_for("a", "../../etc/passwd"), "image/jpeg")
    assert ".." not in key
    assert media_cache.KEY_PATTERN.match(key)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "ab/not-a-digest.jpg",
        "../secret.jpg",
        "ab/" + "0" * 64 + ".svg",
        "ab/" + "0" * 64 + "/../../escape.jpg",
    ],
)
def test_a_malformed_key_resolves_to_nothing(root, key):
    assert path_for(root, key) is None


def test_a_key_pointing_outside_the_root_is_refused(root):
    assert path_for(root, "ab/" + "0" * 64 + ".jpg") is None  # not written yet


# --- what may be stored -----------------------------------------------------


def test_svg_is_never_cacheable():
    """Served from our own origin under `script-src 'self'`, so a cached SVG
    would be stored XSS with the dashboard's own privileges."""
    with pytest.raises(MediaCacheError):
        media_cache.storage_key(digest_for("a", "1"), "image/svg+xml")


@pytest.mark.parametrize("content_type", ["text/html", "application/pdf", "image/gif"])
def test_only_the_three_raster_types_are_cacheable(content_type):
    with pytest.raises(MediaCacheError):
        media_cache.storage_key(digest_for("a", "1"), content_type)


def test_an_empty_or_oversized_image_is_refused_before_anything_is_written(root):
    with pytest.raises(MediaCacheError):
        store(root, digest_for("a", "1"), b"", "image/jpeg")
    with pytest.raises(MediaCacheError):
        store(root, digest_for("a", "1"), b"x" * (media_cache.MAX_IMAGE_BYTES + 1), "image/jpeg")
    assert list(root.rglob("*.jpg")) == []


# --- writing ----------------------------------------------------------------


def test_store_then_read_round_trips(root):
    stored = store(root, digest_for("a", "1"), JPEG, "image/jpeg")
    path = path_for(root, stored.storage_key)

    assert path is not None
    assert path.read_bytes() == JPEG
    assert stored.size_bytes == len(JPEG)
    assert stored.content_type == "image/jpeg"


def test_store_leaves_no_temporary_file_behind(root):
    """Written to a temp name and `os.replace`d, because the cron, an ARQ
    catch-up storm and a request-time fill can all target one path at once."""
    store(root, digest_for("a", "1"), JPEG, "image/jpeg")

    assert [p.name for p in root.rglob("*.tmp-*")] == []


def test_storing_twice_replaces_rather_than_duplicating(root):
    digest = digest_for("a", "1")
    store(root, digest, JPEG, "image/jpeg")
    stored = store(root, digest, JPEG + b"newer", "image/jpeg")

    assert path_for(root, stored.storage_key).read_bytes() == JPEG + b"newer"
    assert len(list(root.rglob("*.jpg"))) == 1


def test_delete_removes_the_file_and_tolerates_a_stale_key(root):
    stored = store(root, digest_for("a", "1"), JPEG, "image/jpeg")

    assert delete(root, stored.storage_key) is True
    assert path_for(root, stored.storage_key) is None
    assert delete(root, stored.storage_key) is False
    assert delete(root, None) is False


def test_directory_bytes_counts_what_is_there(root):
    assert media_cache.directory_bytes(root) == 0
    store(root, digest_for("a", "1"), JPEG, "image/jpeg")
    store(root, digest_for("a", "2"), PNG, "image/png")

    assert media_cache.directory_bytes(root) == len(JPEG) + len(PNG)


# --- reading Meta's URLs ----------------------------------------------------


def test_the_expiry_is_parsed_out_of_oe():
    """`oe` is a hex unix timestamp. Measured live 2026-09-25, docs §2d."""
    assert cdn.expires_at(LIVE_URL) == datetime.fromtimestamp(0x68D6F0AA, tz=UTC)


def test_a_url_without_oe_is_unknown_not_expired():
    """None must mean "no opinion" -- treating it as expired would refetch a
    perfectly good URL on every single pass."""
    assert cdn.expires_at("https://scontent.cdninstagram.com/v/t51/1.jpg") is None
    assert cdn.is_expired("https://scontent.cdninstagram.com/v/t51/1.jpg") is False
    assert cdn.expires_at(None) is None


def test_expiry_is_read_relative_to_now():
    past = "https://scontent.cdninstagram.com/1.jpg?oe=00000001"
    assert cdn.is_expired(past) is True
    assert cdn.is_expired(LIVE_URL, now=datetime.fromtimestamp(1, tz=UTC)) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://instagram.ffjr1-2.fna.fbcdn.net/v/t51/1.jpg",
        "https://scontent.cdninstagram.com/v/t51/1.jpg",
    ],
)
def test_metas_own_media_hosts_are_allowed(url):
    assert cdn.is_media_cdn_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # The SSRF cases. This fetch runs beside these services on the compose
        # network, and the URL it is handed comes out of MongoDB.
        "http://169.254.169.254/latest/meta-data/",
        "http://mongodb:27017/",
        "http://redis:6379/",
        "http://localhost:8000/api/settings",
        "file:///etc/passwd",
        # Plausible-looking but not Meta's.
        "https://fbcdn.net.evil.example/1.jpg",
        "https://evil-cdninstagram.com/1.jpg",
        # http, not https, on a real Meta host.
        "http://scontent.cdninstagram.com/v/t51/1.jpg",
        None,
        "",
    ],
)
def test_everything_else_is_refused(url):
    assert cdn.is_media_cdn_url(url) is False


@pytest.mark.parametrize(
    ("data", "expected"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")],
)
def test_the_content_type_is_read_from_the_bytes(data, expected):
    assert cdn.sniff_content_type(data) == expected


@pytest.mark.parametrize("data", [b"<!doctype html><html>nope", b"", b"RIFF____NOPE0000"])
def test_a_non_image_body_is_not_given_a_type(data):
    """A CDN answering with an HTML error page still sets a content-type
    header; without sniffing it would land on disk labelled as an image."""
    assert cdn.sniff_content_type(data) is None
