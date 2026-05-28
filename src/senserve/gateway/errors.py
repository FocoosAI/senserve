from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from senserve.settings import get_settings


def openai_error_response(
    message: str,
    error_type: str = "invalid_request_error",
    code: str | None = None,
    status_code: int = 400,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {"message": message, "type": error_type, "code": code},
    }
    return JSONResponse(body, status_code=status_code)


def switching_response(
    message: str = "Model switch in progress",
    retry_after: int | None = None,
) -> JSONResponse:
    seconds = retry_after if retry_after is not None else get_settings().switch_retry_after_s
    return JSONResponse(
        {
            "error": {
                "message": message,
                "type": "server_error",
                "code": "model_switching",
            }
        },
        status_code=503,
        headers={"Retry-After": str(seconds)},
    )
