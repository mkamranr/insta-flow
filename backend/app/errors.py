"""Normalised application errors (§53).

Every error the API can raise maps to exactly one of these, so the HTTP layer
never has to guess a status code and internal detail never leaks to the browser.
"""


class SocialFlowError(Exception):
    """Base class. `status_code` is what the API layer returns."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message or self.__doc__ or self.code)
        self.message = message or "Something went wrong."
        self.context = context


class ValidationError(SocialFlowError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(SocialFlowError):
    status_code = 401
    code = "authentication_error"


class AuthorizationError(SocialFlowError):
    status_code = 403
    code = "authorization_error"


class NotFoundError(SocialFlowError):
    status_code = 404
    code = "not_found"


class ConflictError(SocialFlowError):
    status_code = 409
    code = "conflict"


class RateLimitedError(SocialFlowError):
    status_code = 429
    code = "rate_limited"


class MetaAPIError(SocialFlowError):
    status_code = 502
    code = "meta_api_error"

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        error_code: str | int | None = None,
        raw_response: dict[str, object] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.meta_status_code = status_code
        self.error_code = error_code
        self.raw_response = raw_response or {}
        self.retryable = retryable


class MetaRateLimitError(MetaAPIError):
    code = "meta_rate_limit"

    def __init__(self, message: str = "", **kwargs: object) -> None:
        kwargs["retryable"] = True
        super().__init__(message, **kwargs)  # type: ignore[arg-type]


class MetaTokenError(MetaAPIError):
    """Token expired or revoked. Never retried — the account must reconnect."""

    code = "meta_token_error"


class WebhookValidationError(SocialFlowError):
    status_code = 403
    code = "webhook_validation_error"


class CampaignError(SocialFlowError):
    status_code = 400
    code = "campaign_error"


class TemplateError(SocialFlowError):
    status_code = 400
    code = "template_error"


class QueueError(SocialFlowError):
    status_code = 503
    code = "queue_error"


class GoneError(SocialFlowError):
    """The thing existed and no longer does; the caller cannot make it exist again.

    Used for an invite that is genuinely ours but spent -- accepted, revoked or
    past its deadline. A 404 would be the house choice, and elsewhere it is the
    right one: `current_workspace` returns 404 rather than 403 so a slug cannot
    be enumerated. That argument does not apply here, because only a holder of a
    256-bit token ever reaches this path, and a guesser cannot produce a
    once-valid token. Collapsing spent into unknown would instead make the
    common real case -- clicking a link three days late -- indistinguishable
    from a corrupted one, which is the support question that actually happens.
    """

    status_code = 410
    code = "gone"


class MailError(SocialFlowError):
    """A send failed. `retryable` decides whether the worker tries again.

    Note the inversion against `MetaAPIError`, which is built from HTTP where
    `retryable = status >= 500`. SMTP is the other way round (RFC 5321 §4.2.1):
    4xx is a transient "try later" and 5xx is permanent. The classifier in
    `services/email/client.py` is the only place that decides, and
    `tests/test_email_retry.py` pins every case.
    """

    status_code = 502
    code = "mail_error"

    def __init__(
        self,
        message: str = "",
        *,
        smtp_code: int | None = None,
        retryable: bool = False,
        auth_refused: bool = False,
        refused: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.smtp_code = smtp_code
        self.retryable = retryable
        # Set only for a rejected credential. Surfaced on the Settings card
        # rather than used to block later sends -- see the job's docstring.
        self.auth_refused = auth_refused
        self.refused = refused
