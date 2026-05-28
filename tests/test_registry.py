from senserve.registry import load_registry


def test_load_registry_two_models(models_toml):
    reg = load_registry(models_toml)
    assert len(reg.models) == 2
    qwen = reg.get("qwen3-vl-4b-awq")
    assert qwen.vllm.get("gpu_memory_utilization") == 0.2
    assert qwen.has_capability("video")
    gemma = reg.get("gemma-4-26b-a4b-it")
    assert not gemma.has_capability("video")
    assert gemma.has_capability("image")
