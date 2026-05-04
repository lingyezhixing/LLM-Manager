from __future__ import annotations

import re


def extract_tokens_from_streaming(line: str) -> dict | None:
    if not line or not line.startswith("data: "):
        return None
    data = line[6:].strip()
    if data == "[DONE]":
        return None
    import json
    try:
        obj = json.loads(data)
        return obj.get("usage")
    except (json.JSONDecodeError, AttributeError):
        return None
