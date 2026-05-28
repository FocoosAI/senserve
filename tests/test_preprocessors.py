import pytest

from senserve.preprocessors import CapabilityError, preprocess_messages
from senserve.registry import load_registry


@pytest.fixture
def registry(models_toml):
    return load_registry(models_toml)


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
