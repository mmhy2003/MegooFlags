from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, JsonValue
from sqlalchemy.exc import IntegrityError


class ErrorBody(BaseModel):
    code: str
    message: str
    details: JsonValue = None


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: JsonValue = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def not_found(entity: str, key: str) -> ApiError:
    return ApiError(404, "not_found", f"{entity} '{key}' not found")


def key_conflict(entity: str, key: str) -> ApiError:
    return ApiError(409, "key_conflict", f"{entity} '{key}' already exists")


def version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "version_conflict",
        f"the config was changed by someone else (current version {current})",
        {"version": current},
    )


def in_use(message: str, used_by: list[dict[str, str]]) -> ApiError:
    details: JsonValue = {"used_by": [dict(u) for u in used_by]}
    return ApiError(409, "in_use", message, details)


def not_archived(key: str) -> ApiError:
    return ApiError(409, "not_archived", f"flag '{key}' must be archived before it is deleted")


def invalid_reference(message: str, path: str) -> ApiError:
    return ApiError(422, "invalid_reference", message, {"path": path})


def validation_error(message: str, path: str | None = None) -> ApiError:
    return ApiError(422, "validation_error", message, None if path is None else {"path": path})


def prerequisite_cycle(message: str, path: str) -> ApiError:
    return ApiError(422, "prerequisite_cycle", message, {"path": path})


def weak_password(message: str) -> ApiError:
    return ApiError(422, "weak_password", message, {"path": "password"})


def exceeds_owner_rights(permissions: list[str]) -> ApiError:
    return ApiError(
        422,
        "exceeds_owner_rights",
        "a personal token cannot hold permissions you do not hold at its scope",
        {"permissions": list(permissions)},
    )


def unauthorized(message: str) -> ApiError:
    return ApiError(401, "unauthorized", message, headers={"WWW-Authenticate": "Bearer"})


def invalid_credentials() -> ApiError:
    return ApiError(401, "invalid_credentials", "invalid email or password")


def already_set_up() -> ApiError:
    return ApiError(409, "already_set_up", "this instance already has a user; sign in instead")


def builtin_role(key: str) -> ApiError:
    return ApiError(409, "builtin_role", f"role '{key}' is built in and cannot be changed")


def last_admin() -> ApiError:
    return ApiError(
        409, "last_admin", "this would leave no active user with the admin role at org scope"
    )


def no_password() -> ApiError:
    return ApiError(409, "no_password", "this account has no password; it signs in another way")


def key_inactive(entity: str, key: str) -> ApiError:
    return ApiError(409, "key_inactive", f"{entity} '{key}' is revoked or expired")


def invalid_invitation() -> ApiError:
    # The same answer for unknown, used, superseded and expired links, so none can be told apart.
    return ApiError(404, "invalid_invitation", "this link is invalid or has expired")


def forbidden(message: str) -> ApiError:
    return ApiError(403, "forbidden", message)


def mfa_required() -> ApiError:
    return ApiError(401, "mfa_required", "a second factor is required to finish signing in")


def mfa_enrolment_required() -> ApiError:
    return ApiError(
        403, "mfa_enrolment_required", "set up a second factor before using this endpoint"
    )


def invalid_code() -> ApiError:
    return ApiError(401, "invalid_code", "the code is wrong, expired or already used")


def reauth_required() -> ApiError:
    return ApiError(
        403,
        "reauth_required",
        "verify a second factor again (sign in anew) and retry within 10 minutes",
    )


def totp_exists() -> ApiError:
    return ApiError(409, "totp_exists", "an authenticator app is already set up; remove it first")


def mfa_required_by_org() -> ApiError:
    return ApiError(
        409, "mfa_required_by_org", "the organisation requires a second factor; keep at least one"
    )


def public_url_required() -> ApiError:
    return ApiError(
        409,
        "public_url_required",
        "set MEGOOFLAGS_PUBLIC_URL to use passkeys and single sign-on",
    )


def unavailable(message: str) -> ApiError:
    return ApiError(503, "unavailable", message)


def csrf_failed() -> ApiError:
    return ApiError(403, "csrf_failed", "the request origin is not allowed")


def rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "rate_limited",
        f"too many requests; retry in {retry_after} s",
        headers={"Retry-After": str(retry_after)},
    )


def payload_too_large(message: str) -> ApiError:
    return ApiError(413, "payload_too_large", message)


def integrity_error_status_code(sqlstate: str | None) -> tuple[int, str]:
    """Map a Postgres integrity violation that escaped the services' own checks."""
    match sqlstate:
        case "23505":  # unique_violation
            return 409, "key_conflict"
        case "23503":  # foreign_key_violation
            return 409, "in_use"
        case "23514":  # check_violation
            return 422, "validation_error"
        case _:
            return 500, "internal_error"


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody},
    403: {"model": ErrorBody},
    404: {"model": ErrorBody},
    409: {"model": ErrorBody},
    422: {"model": ErrorBody},
}

SDK_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody},
    403: {"model": ErrorBody},
    413: {"model": ErrorBody},
    422: {"model": ErrorBody},
    429: {"model": ErrorBody},
}


def _error_response(
    status: int,
    code: str,
    message: str,
    details: JsonValue = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorBody(code=code, message=message, details=details)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"), headers=headers)


async def _api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return _error_response(exc.status, exc.code, exc.message, exc.details, exc.headers)


async def _request_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details: JsonValue = [
        {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
    ]
    return _error_response(422, "validation_error", "request validation failed", details)


async def _integrity_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, IntegrityError)
    status, code = integrity_error_status_code(getattr(exc.orig, "sqlstate", None))
    message = "internal error" if status == 500 else "the change conflicts with existing data"
    return _error_response(status, code, message)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _request_validation_error)
    app.add_exception_handler(IntegrityError, _integrity_error)
