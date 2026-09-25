# Errors the routers raise, and the handler that turns them into the error
# JSON the contract specifies.
#
# main.py calls register_error_handlers() once, so any route can just raise
# and the right response comes out.
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.models import ApiError


class ApiException(Exception):
    """Base for every error a route can raise."""

    status_code: int = 400

    def __init__(self, error: str, message: str):
        self.error = error  # short code for machines, e.g. "UNKNOWN_SKU"
        self.message = message  # sentence for a person to read


class InvalidRequestError(ApiException):
    """The request itself was malformed."""

    status_code = 400


class NotFoundError(ApiException):
    """The thing asked for does not exist."""

    status_code = 404


class ConflictError(ApiException):
    """It exists, but its current state forbids this operation."""

    status_code = 409


async def _handle_api_exception(request: Request, exc: ApiException) -> JSONResponse:
    """Renders any ApiException as the contract's {error, message} body."""
    body = ApiError(error=exc.error, message=exc.message)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(by_alias=True))


def register_error_handlers(app: FastAPI) -> None:
    """Wires the handler above onto the app. Call once, at startup."""
    app.add_exception_handler(ApiException, _handle_api_exception)
