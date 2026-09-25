# Failures the business layers can report, with no HTTP anywhere in them.
#
# A closed basket is a conflict whether the caller arrived over HTTP or not,
# so these carry no status codes. Turning them into responses is the API
# layer's job, in api/error_handlers.py.


class DomainError(Exception):
    """Base for every failure the business layers raise."""

    def __init__(self, error: str, message: str):
        super().__init__(message)
        self.error = error  # short code for machines, e.g. "UNKNOWN_SKU"
        self.message = message  # sentence for a person to read


class NotFoundError(DomainError):
    """The thing asked for does not exist."""


class ConflictError(DomainError):
    """It exists, but its current state forbids this operation."""


class InvalidRequestError(DomainError):
    """The request itself was malformed."""
