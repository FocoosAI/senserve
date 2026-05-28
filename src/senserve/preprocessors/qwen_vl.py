from __future__ import annotations

import copy
from typing import Any

from senserve.preprocessors.base import Preprocessor, register
from senserve.preprocessors.media_utils import expand_message_parts
from senserve.registry import ModelSpec
from senserve.settings import get_settings


@register("qwen_vl")
class QwenVLPreprocessor(Preprocessor):
    def process_messages(
        self, messages: list[dict[str, Any]], spec: ModelSpec
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        out: list[dict[str, Any]] = []
        for msg in messages:
            m = copy.deepcopy(msg)
            content = m.get("content")
            if isinstance(content, list):
                m["content"] = expand_message_parts(
                    content,
                    settings.video_max_frames,
                    settings.max_upload_bytes,
                    settings.temp_dir,
                )
            out.append(m)
        return out
