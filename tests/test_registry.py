import pytest

from senserve.registry import load_registry


def test_load_registry_two_models(models_config):
    reg = load_registry(models_config)
    assert len(reg.models) == 2
    qwen = reg.get("qwen3-vl-4b-awq")
    assert qwen.vllm.get("gpu_memory_utilization") == 0.2
    assert qwen.has_capability("video")
    gemma = reg.get("gemma-4-26b-a4b-it")
    assert not gemma.has_capability("video")
    assert gemma.has_capability("image")


def test_load_registry_rejects_toml(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text("[models]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        load_registry(path)


def test_load_registry_local_overlay_merges(tmp_path):
    base = tmp_path / "models.yaml"
    base.write_text(
        """defaults:
  gpu_memory_utilization: 0.2
models:
  - id: qwen3-vl-4b-awq
    source: base/source
    capabilities: [text]
""",
        encoding="utf-8",
    )
    local = tmp_path / "models.local.yaml"
    local.write_text(
        """defaults:
  gpu_memory_utilization: 0.5
models:
  - id: extra-model
    source: local/source
    capabilities: [text]
""",
        encoding="utf-8",
    )
    reg = load_registry(base)
    assert reg.get("qwen3-vl-4b-awq").vllm["gpu_memory_utilization"] == 0.5
    assert "extra-model" in reg.models


def test_load_production_models_yaml():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    reg = load_registry(repo_root / "config" / "models.yaml")
    assert "qwen3.5-0.8b" in reg.models
    qwen = reg.get("qwen3.5-0.8b")
    assert qwen.vllm.get("limit-mm-per-prompt.video") == 1
    assert qwen.vllm.get("allowed_local_media_path") == "/datasets"


def test_load_registry_dotted_vllm_keys(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        """defaults:
  "limit-mm-per-prompt.video": 2
models:
  - id: m1
    source: hf/x
    capabilities: [text]
""",
        encoding="utf-8",
    )
    reg = load_registry(path)
    assert reg.get("m1").vllm["limit-mm-per-prompt.video"] == 2
