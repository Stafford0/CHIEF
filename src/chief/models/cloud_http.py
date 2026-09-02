from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

JsonTransport = Callable[[str, dict[str, str], bytes, float, int], tuple[int, dict[str, Any]]]


def post_json(
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout_seconds: float,
    max_response_bytes: int,
) -> tuple[int, dict[str, Any]]:
    """POST bounded JSON without adding another runtime dependency."""

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(max_response_bytes + 1)
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        payload = exc.read(max_response_bytes + 1)
        status = int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Cloud model request failed: {exc}") from exc

    if len(payload) > max_response_bytes:
        raise RuntimeError("Cloud model response exceeded the configured size limit.")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Cloud model provider returned malformed JSON.") from exc
    if not isinstance(decoded, dict):
        raise TypeError("Cloud model provider returned an unexpected JSON shape.")
    return status, decoded
