from senserve.engine import _vllm_cmd
from senserve.registry import ModelSpec


def test_vllm_cmd_dotted_flags_and_booleans():
    spec = ModelSpec(
        id="qwen3.5-0.8b",
        display_name="Qwen3.5 0.8B",
        source="Qwen/Qwen3.5-0.8B",
        capabilities=frozenset({"text"}),
        preprocessor="none",
        enabled=True,
        vllm={
            "trust_remote_code": True,
            "skip_mm_profiling": True,
            "max_model_len": "auto",
            "gpu_memory_utilization": 0.85,
            "limit-mm-per-prompt.video": 1,
            "structured-outputs-config.backend": "guidance",
            "structured-outputs-config.disable_any_whitespace": True,
            "enable_auto_tool_choice": True,
        },
    )
    cmd = _vllm_cmd(spec, 8000, {}, sleep_enabled=False)
    assert cmd[:4] == ["vllm", "serve", "Qwen/Qwen3.5-0.8B", "--host"]
    assert "--trust-remote-code" in cmd
    assert "--skip-mm-profiling" in cmd
    assert "--max-model-len" in cmd and "auto" in cmd
    assert "--gpu-memory-utilization" in cmd and "0.85" in cmd
    assert "--limit-mm-per-prompt.video" in cmd
    assert "--structured-outputs-config.backend" in cmd
    assert "guidance" in cmd
    assert "--structured-outputs-config.disable_any_whitespace" in cmd
    assert "--enable-auto-tool-choice" in cmd
