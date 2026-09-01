"""Normalised application errors (§53).

Every error the API can raise maps to exactly one of these, so the HTTP layer
never has to guess a status code and internal detail never leaks to the browser.
"""


class InstaFlowError(Exception):
    """Base class. `status_code` is what the API layer returns."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message or self.__doc__ or self.code)
        self.message = message or "Something went wrong."
        self.context = context


class ValidationError(InstaFlowError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(InstaFlowError):
    status_code = 401
    code = "authentication_error"


class AuthorizationError(InstaFlowError):
    status_code = 403
    code = "authorization_error"


class NotFoundError(InstaFlowError):
    status_code = 404
    code = "not_found"


class ConflictError(InstaFlowError):
    status_code = 409
    code = "conflict"


class RateLimitedError(InstaFlowError):
    status_code = 429
    code = "rate_limited"


class MetaAPIError(InstaFlowError):
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


class WebhookValidationError(InstaFlowError):
    status_code = 403
    code = "webhook_validation_error"


class CampaignError(InstaFlowError):
    status_code = 400
    code = "campaign_error"


class TemplateError(InstaFlowError):
    status_code = 400
    code = "template_error"


class QueueError(InstaFlowError):
    status_code = 503
    code = "queue_error"
