import pytest

from senserve.preprocessors import CapabilityError, preprocess_messages
from senserve.registry import load_registry
from senserve.settings import get_settings


@pytest.fixture
def registry(models_config):
    return load_registry(models_config)


def test_video_rejected_for_gemma(registry):
    spec = registry.get("gemma-4-26b-a4b-it")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,AAAA"}},
            ],
        }
    ]
    with pytest.raises(CapabilityError):
        preprocess_messages(messages, spec)


def test_image_allowed_for_gemma(registry):
    spec = registry.get("gemma-4-26b-a4b-it")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ]
    preprocess_messages(messages, spec)


def test_passthrough_when_inline_disabled(registry, monkeypatch):
    monkeypatch.setenv("SENSERVE_INLINE_REMOTE_MEDIA", "0")
    get_settings.cache_clear()
    spec = registry.get("qwen3-vl-4b-awq")
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "video_url",
                    "video_url": {"url": "file:///datasets/foo.mp4"},
                },
            ],
        }
    ]
    assert preprocess_messages(messages, spec) is messages
    get_settings.cache_clear()


def test_http_video_inlined_when_enabled(registry, monkeypatch):
    monkeypatch.setenv("SENSERVE_INLINE_REMOTE_MEDIA", "1")
    get_settings.cache_clear()
    spec = registry.get("qwen3-vl-4b-awq")
    # Invalid base64 but we only test that file:// is untouched when inline on
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "video_url", "video_url": {"url": "file:///datasets/foo.mp4"}},
            ],
        }
    ]
    out = preprocess_messages(messages, spec)
    assert out[0]["content"][0]["video_url"]["url"] == "file:///datasets/foo.mp4"
    get_settings.cache_clear()
