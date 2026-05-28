from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


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


def switching_response(message: str = "Model switch in progress") -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": message,
                "type": "server_error",
                "code": "model_switching",
            }
        },
        status_code=503,
        headers={"Retry-After": "30"},
    )
