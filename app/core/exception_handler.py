"""Global exception handler used by app/main.py.

Lives here (not in main.py) so it can be unit-tested in isolation without
booting the FastAPI app, its lifespan, Mongo/Redis, etc.

FastAPI's built-in handlers handle HTTPException / RequestValidationError;
this handler is reached only for genuinely unexpected runtime exceptions.
We always 500 in that case, but enrich the response with the exception TYPE
(always) and — only in DEBUG mode — the exception message, so server-side
issues are diagnosable without grepping logs.
"""

from __future__ import annotations

import logging
from typing import Callable, Awaitable

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("app.exception_handler")


def build_global_exception_handler(
    *,
    debug: bool,
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Build the catch-all exception handler.

    Args:
        debug: when True, include the exception message in the response body
               (useful for local/dev). When False, surface only the exception
               TYPE name so production responses don't leak internals.
    """

    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        exc_type = type(exc).__name__
        logger.error(f"Unhandled {exc_type}: {exc}", exc_info=True)

        if debug:
            message = f"{exc_type}: {exc}"
        else:
            message = "Internal server error occurred"

        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "exception_type": exc_type,
                    "message": message,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    return _handler
