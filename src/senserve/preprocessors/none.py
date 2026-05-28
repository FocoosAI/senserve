from __future__ import annotations

from typing import Any

from senserve.preprocessors.base import Preprocessor, register
from senserve.registry import ModelSpec


@register("none")
class NonePreprocessor(Preprocessor):
    def process_messages(
        self, messages: list[dict[str, Any]], spec: ModelSpec
    ) -> list[dict[str, Any]]:
        return messages
