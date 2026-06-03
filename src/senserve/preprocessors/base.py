"""Lightweight chat message checks before proxying to vLLM."""

from __future__ import annotations

import copy
from typing import Any

from senserve.preprocessors.media_utils import normalize_message_parts
from senserve.registry import ModelSpec
from senserve.settings import get_settings


class CapabilityError(ValueError):
    """Request uses a modality the active model cannot handle."""


def validate_capabilities(messages: list[dict[str, Any]], spec: ModelSpec) -> None:
    """Reject requests that use modalities the model does not support."""
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type", "")
            if ptype in ("video_url", "video") and not spec.has_capability("video"):
                raise CapabilityError(
                    f"Model {spec.id} does not support video; capabilities: {spec.capabilities}"
                )
            if ptype in ("image_url", "image") and not (
                spec.has_capability("vision") or spec.has_capability("image")
            ):
                raise CapabilityError(
                    f"Model {spec.id} does not support images; capabilities: {spec.capabilities}"
                )


def preprocess_messages(
    messages: list[dict[str, Any]], spec: ModelSpec
) -> list[dict[str, Any]]:
    """Optional capability check and HTTP video inlining; otherwise pass through."""
    settings = get_settings()
    if settings.validate_capabilities:
        validate_capabilities(messages, spec)
    if not settings.inline_remote_media:
        return messages

    max_bytes = settings.max_upload_bytes
    out: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        normalized = normalize_message_parts(content, max_bytes)
        if normalized is content:
            out.append(msg)
            continue
        m = copy.deepcopy(msg)
        m["content"] = normalized
        out.append(m)
    return out
