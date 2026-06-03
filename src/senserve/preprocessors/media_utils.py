"""Fetch and normalize multimodal message parts for vLLM."""

from __future__ import annotations

import base64
from typing import Any
from urllib.request import urlopen


def parse_data_url(url: str) -> tuple[bytes, str]:
    if not url.startswith("data:"):
        raise ValueError("Invalid data URL; expected base64 encoding")
    header, _, payload = url.partition(",")
    if "base64" not in header:
        raise ValueError("Invalid data URL; expected base64 encoding")
    mime = "application/octet-stream"
    if ";" in header:
        mime = header[5:].split(";")[0] or mime
    try:
        return base64.b64decode(payload), mime
    except Exception as exc:
        raise ValueError("Invalid base64 in data URL") from exc


def fetch_url_bytes(url: str, max_bytes: int) -> tuple[bytes, str]:
    if url.startswith("data:"):
        data, mime = parse_data_url(url)
    else:
        with urlopen(url, timeout=60) as resp:  # noqa: S310 — user-provided URLs
            data = resp.read(max_bytes + 1)
            mime = resp.headers.get_content_type() or "application/octet-stream"
    if len(data) > max_bytes:
        raise ValueError(f"Attachment exceeds {max_bytes} bytes")
    return data, mime


def normalize_message_parts(parts: list[Any], max_bytes: int) -> list[Any]:
    """Keep video_url parts for vLLM; inline remote URLs as base64 data URLs."""
    changed = False
    out: list[Any] = []
    for part in parts:
        if not isinstance(part, dict):
            out.append(part)
            continue
        ptype = part.get("type", "")
        if ptype not in ("video_url", "video"):
            out.append(part)
            continue
        normalized = _normalize_video_part(part, max_bytes)
        if normalized is not part:
            changed = True
        out.append(normalized)
    return parts if not changed else out


def _normalize_video_part(part: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    key = "video_url" if part.get("type") == "video_url" else "video"
    block = part.get(key) or {}
    if not isinstance(block, dict):
        raise ValueError("Invalid video part")
    url = block.get("url", "")
    if url.startswith("file:"):
        return part
    if url.startswith("data:"):
        fetch_url_bytes(url, max_bytes)
        return part
    if not url.startswith(("http://", "https://")):
        return part
    data, mime = fetch_url_bytes(url, max_bytes)
    encoded = base64.standard_b64encode(data).decode("ascii")
    return {**part, key: {**block, "url": f"data:{mime};base64,{encoded}"}}

