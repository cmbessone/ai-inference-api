"""Formato Server-Sent Events (funciones puras)."""

from __future__ import annotations

import json
from collections.abc import Mapping

DONE = b"data: [DONE]\n\n"


def sse_event(payload: Mapping[str, object]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(", ", ": "))
    return f"data: {data}\n\n".encode()


def token_event(token: str, index: int) -> bytes:
    return sse_event({"token": token, "index": index})


def error_event(code: str, retry_after_s: int | None = None) -> bytes:
    payload: dict[str, object] = {"error": code}
    if retry_after_s is not None:
        payload["retry_after_s"] = retry_after_s
    return sse_event(payload)
