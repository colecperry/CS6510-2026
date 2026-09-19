# Custom exceptions that routers raise, auto-converted into ApiError JSON.
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.models import ApiError


# Base error: carries the two fields ApiError needs, plus a status code.
class ApiException(Exception):
    status_code: int = 400

    def __init__(self, error: str, message: str):
        """Stores the error code and message a router wants to report.

        Takes: error (short machine-readable code), message (human-readable text).
        Returns: nothing.
        """
        self.error = error
        self.message = message


# One subclass per status code routers need to raise.
class InvalidRequestError(ApiException):
    status_code = 400


class NotFoundError(ApiException):
    status_code = 404


class ConflictError(ApiException):
    status_code = 409


# Runs automatically whenever any route raises an ApiException.
async def _handle_api_exception(request: Request, exc: ApiException) -> JSONResponse:
    """Converts a raised ApiException into the spec's error JSON shape.

    Takes: the incoming request and the exception that was raised.
    Returns: a JSONResponse with the right status code and ApiError body.
    """
    body = ApiError(error=exc.error, message=exc.message)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(by_alias=True))


# Called once at app startup to wire the handler above into FastAPI.
def register_error_handlers(app: FastAPI) -> None:
    """Registers the handler above so FastAPI uses it for every ApiException.

    Takes: the FastAPI app instance.
    Returns: nothing.
    """
    app.add_exception_handler(ApiException, _handle_api_exception)
