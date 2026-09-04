"""First-run admin setup.

A deployment with no shell access -- a container on someone else's cloud --
cannot run `make bootstrap-admin`, so the first admin has to be creatable
through the browser. That is a dangerous thing to leave lying around, so it is
constrained hard: it works only while the database holds no users at all, only
when a secret is configured out of band, and only once.
"""

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.services.security.passwords import MIN_PASSWORD_LENGTH

# A secret short enough to guess is worse than none, because it looks like
# protection. Long enough that the throttle plus this length is not brute
# forceable in any practical time.
MIN_SETUP_SECRET_LENGTH = 16


class SetupSecretRequest(BaseModel):
    """Step one: prove you hold the secret, before the form is shown."""

    secret: str = Field(min_length=1, max_length=512)


class SetupAdminRequest(BaseModel):
    """Step two. The secret is required again.

    The first step is a convenience for the operator, not a gate: a caller can
    skip it entirely, so the real check has to happen here.
    """

    secret: str = Field(min_length=1, max_length=512)
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=512)
    password_confirm: str = Field(min_length=1, max_length=512)

    @field_validator("email")
    @classmethod
    def normalise(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def passwords_match(self) -> "SetupAdminRequest":
        # Checked server-side as well as in the form: a mistyped password that
        # only the browser validated would lock the operator out of the one
        # account they can never recreate.
        if self.password != self.password_confirm:
            raise ValueError("Passwords do not match.")
        return self
