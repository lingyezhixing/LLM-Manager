"""Uniform error contract (replaces the old mix of {success:False}, JSONResponse, HTTPException)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, *, status_code: int, message: str, type: str = "api_error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.type = type

    def payload(self) -> dict[str, Any]:
        return {"error": {"type": self.type, "message": self.message}}


def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(status_code=exc.status_code, content=exc.payload())
