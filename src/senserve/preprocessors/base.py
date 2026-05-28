"""Preprocessor registry and capability checks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from senserve.registry import ModelSpec

_REGISTRY: dict[str, type[Preprocessor]] = {}


class CapabilityError(ValueError):
    """Request uses a modality the active model cannot handle."""


class Preprocessor(ABC):
    @abstractmethod
    def process_messages(
        self, messages: list[dict[str, Any]], spec: ModelSpec
    ) -> list[dict[str, Any]]:
        ...


def register(name: str):
    def deco(cls: type[Preprocessor]) -> type[Preprocessor]:
        _REGISTRY[name] = cls
        return cls

    return deco


def get_preprocessor(name: str) -> Preprocessor:
    try:
        return _REGISTRY[name]()
    except KeyError as exc:
        raise KeyError(f"Unknown preprocessor: {name}") from exc


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
    validate_capabilities(messages, spec)
    return get_preprocessor(spec.preprocessor).process_messages(messages, spec)


# Import implementations to register.
from senserve.preprocessors import gemma4, none, qwen_vl  # noqa: E402, F401
