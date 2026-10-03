from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .adapters import DependencyUnavailable
from .chain import ChainUnavailable


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        operation_id: str | None = None,
        details: Any = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.operation_id = operation_id
        self.details = details


def error_body(code: str, message: str, operation_id: str | None = None, details: Any = None):
    return {
        "error": {
            "code": code,
            "message": message,
            "operationId": operation_id,
            "details": details,
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.operation_id, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        safe_errors = [
            {"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "Request validation failed", details=safe_errors),
        )

    @app.exception_handler(DependencyUnavailable)
    async def dependency_error_handler(request: Request, exc: DependencyUnavailable):
        return JSONResponse(
            status_code=503,
            content=error_body("dependency_unavailable", str(exc)),
        )

    @app.exception_handler(ChainUnavailable)
    async def chain_error_handler(request: Request, exc: ChainUnavailable):
        return JSONResponse(status_code=503, content=error_body("chain_unavailable", str(exc)))

    @app.exception_handler(StarletteHTTPException)
    async def framework_http_error_handler(request: Request, exc: StarletteHTTPException):
        code = {
            404: "route_not_found",
            405: "method_not_allowed",
            413: "request_too_large",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, "Request could not be processed"),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "An internal error occurred"),
        )
