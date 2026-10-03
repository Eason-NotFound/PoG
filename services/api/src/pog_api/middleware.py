from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import error_body
from .file_store import MAX_FILE_BYTES


class RequestBodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(status_code=413, detail="Upload request too large")


class UploadBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, overhead_bytes: int = 64 * 1024):
        self.app = app
        self.limit = MAX_FILE_BYTES + overhead_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/v2/documents"
        ):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.limit:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await self._reject(scope, receive, send)
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise RequestBodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content=error_body("request_too_large", "Upload request exceeds the 10 MiB file limit"),
        )
        await response(scope, receive, send)
