from unittest.mock import patch

from senserve import vllm_flags


SAMPLE_HELP = """
usage: vllm serve [model]

options:
  --host HOST
  --port PORT
  --gpu-memory-utilization GPU_MEMORY_UTILIZATION
  --limit-mm-per-prompt.video VIDEO
"""


@patch("senserve.vllm_flags.subprocess.run")
def test_list_vllm_flags_parses_help(mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = SAMPLE_HELP
    vllm_flags._cache = None
    flags = vllm_flags.list_vllm_flags(refresh=True)
    names = {f["yaml_name"] for f in flags}
    assert "gpu_memory_utilization" in names
    assert "limit-mm-per-prompt.video" in names
