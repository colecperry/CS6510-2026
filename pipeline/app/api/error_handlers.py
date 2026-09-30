# The only place in the project that knows HTTP status codes exist.
#
# The business layers raise plain domain errors; this turns each kind into
# the right status code and the {error, message} body the contract requires.
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import ConflictError, DomainError, InvalidRequestError, NotFoundError
from app.schemas import ApiError

_STATUS_BY_ERROR = {
    InvalidRequestError: 400,
    NotFoundError: 404,
    ConflictError: 409,
}


async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    status = _STATUS_BY_ERROR.get(type(exc), 400)
    body = ApiError(error=exc.error, message=exc.message)
    return JSONResponse(status_code=status, content=body.model_dump(by_alias=True))


def register_error_handlers(app: FastAPI) -> None:
    """Wires the handler above onto the app. Call once, at startup."""
    app.add_exception_handler(DomainError, _handle_domain_error)
