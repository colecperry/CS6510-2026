# Turns a domain error into the HTTP response the contract specifies.
#
# Shared by all three services so they cannot disagree about what a conflict looks like. The gateway does not use this: it forwards whatever a service already produced, without interpreting it.
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from common.errors import ConflictError, DomainError, InvalidRequestError, NotFoundError
from common.schemas import ApiError

# The whole mapping. Business code picks the kind of failure; this picks the number the caller sees.
_STATUS_BY_ERROR = {
    InvalidRequestError: 400,
    NotFoundError: 404,
    ConflictError: 409,
}


async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    status = _STATUS_BY_ERROR.get(type(exc), 400)          # 400 if unrecognised
    body = ApiError(error=exc.error, message=exc.message)
    return JSONResponse(status_code=status, content=body.model_dump(by_alias=True))


def register_error_handlers(app: FastAPI) -> None:
    """Wires the handler above onto a service. Call once, at startup."""
    # After this, any route can just raise one of our errors and the right response comes out. No route needs a try/except.
    app.add_exception_handler(DomainError, _handle_domain_error)
