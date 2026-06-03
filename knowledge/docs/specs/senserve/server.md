---
id: SPEC-senserve-server
title: Senserve server
description: Multimodal OpenAI-compatible gateway on port 8787. Proxies chat/models to a lazy vLLM pool (sleep L2); optional capability checks and HTTP video inlining (off by default); per-model preprocessors removed; datasets mount for file:// media; batch script infer_dataset_videos.py.
status: accepted
type: spec
domain: senserve
tags: [senserve, architecture, runbook, vllm, openai]
related:
  - adr:ADR-0002-vllm-worker-no-ray
  - adr:ADR-0003-vllm-sleep-mode-pool
  - spec:SPEC-000-keep
  - code:src/senserve/engine.py
  - code:src/senserve/vllm_sleep.py
  - code:src/senserve/gateway/openai_routes.py
  - code:src/senserve/preprocessors/base.py
  - code:src/senserve/preprocessors/media_utils.py
  - code:scripts/infer_dataset_videos.py
  - test:tests/test_gateway_switch.py matching test_chat_rejects_when_switching
  - test:tests/test_engine_sleep.py matching test_vllm_cmd_includes_sleep_flag
  - test:tests/test_preprocessors.py matching test_passthrough_when_inline_disabled
anchors:
  - id: api-port
    claim: Gateway listens on SENSERVE_API_PORT default 8787
    kind: const
    file: src/senserve/settings.py
    symbol: api_port
    value: "8787"
  - id: worker-base-port
    claim: Compose sets SENSERVE_WORKER_BASE_PORT to 8000
    kind: manual
    file: compose.yaml
    notes: "Per-model ports are base+index when sleep mode enabled"
  - id: switch-retry-after
    claim: Default Retry-After for model switch is 30 seconds
    kind: const
    file: src/senserve/settings.py
    symbol: switch_retry_after_s
    value: "30"
  - id: switching-503
    claim: Model switch returns 503 with configurable Retry-After header
    kind: function
    file: src/senserve/gateway/errors.py
    symbol: switching_response
    signature: '(message: str = "Model switch in progress", retry_after: int | None = None) -> JSONResponse'
  - id: sleep-flag-test
    claim: vLLM cmd includes --enable-sleep-mode when sleep enabled
    kind: test
    file: tests/test_engine_sleep.py
    symbol: test_vllm_cmd_includes_sleep_flag
  - id: chat-switch-test
    claim: Chat completions rejected with 503 while engine is switching
    kind: test
    file: tests/test_gateway_switch.py
    symbol: test_chat_rejects_when_switching
  - id: catalog-model-status
    claim: Per-model catalog status uses worker state, starting when switch targets model, else cold
    kind: function
    file: src/senserve/gateway/openai_routes.py
    symbol: _catalog_model_status
    signature: '(model_id: str, workers_by_id: dict[str, WorkerInfo], st: EngineStatus) -> str'
  - id: list-models-extensions-test
    claim: test_list_models asserts source, status, loaded, and capabilities on catalog entries
    kind: test
    file: tests/test_gateway_switch.py
    symbol: test_list_models
  - id: ui-dashboard
    claim: Operational dashboard is served at GET /ui/ via StaticFiles on gateway static/ui
    kind: manual
    file: src/senserve/gateway/app.py
    notes: "HTML/JS polls /health, /v1/models, /v1/admin/models/status; POST load for switch"
  - id: ui-static-test
    claim: test_ui_index returns 200 for GET /ui/
    kind: test
    file: tests/test_ui_static.py
    symbol: test_ui_index
  - id: compose-hf-cache
    claim: Compose bind-mounts host Hugging Face and vLLM caches into the container
    kind: manual
    file: compose.yaml
    notes: "HF_HOME=/root/.cache/huggingface; volumes ~/.cache/huggingface and ~/.cache/vllm"
  - id: compose-datasets-mount
    claim: Compose mounts host ./datasets read-only at /datasets in the senserve service
    kind: manual
    file: compose.yaml
    notes: "volume ./datasets:/datasets:ro; pairs with allowed_local_media_path in models.toml"
  - id: inline-remote-media-default
    claim: inline_remote_media defaults to false (transparent proxy to vLLM)
    kind: const
    file: src/senserve/settings.py
    symbol: inline_remote_media
    value: "False"
  - id: compose-inline-media-off
    claim: Compose sets SENSERVE_INLINE_REMOTE_MEDIA to 0
    kind: manual
    file: compose.yaml
    notes: "environment SENSERVE_INLINE_REMOTE_MEDIA: \"0\""
  - id: allowed-local-media-path
    claim: vLLM allowed_local_media_path defaults to /datasets in models.toml
    kind: manual
    file: config/models.toml
    notes: "allowed_local_media_path = \"/datasets\" under [defaults]"
  - id: preprocess-passthrough-test
    claim: Chat preprocess leaves messages unchanged when inline remote media is off
    kind: test
    file: tests/test_preprocessors.py
    symbol: test_passthrough_when_inline_disabled
---

# Senserve server

## Goal

Expose a stable OpenAI-compatible HTTP API that routes multimodal chat to whichever vLLM model is active, loading models on demand without Ray.

## Architecture

```
Client / Open WebUI
        │
        ▼  :8787  FastAPI (senserve.gateway)
        │         capability check + optional media URL inline → proxy
        ▼  :8000+N  vllm serve pool (sleep/wake per model)
```

- **Registry**: `config/models.toml` (+ optional `config/models.local.toml`) defines model ids, HF sources, capabilities; shared vLLM flags under `[defaults]` (including `allowed_local_media_path`), per-model overrides in `[[models]].vllm`. No per-model `preprocessor` field — gateway does not reshape video into frames.
- **Chat preprocess** (`preprocess_messages`): optional registry capability guard; optional HTTP(S) `video_url` → base64 data URL when `SENSERVE_INLINE_REMOTE_MEDIA=1`. `data:` and `file://` video URLs are forwarded unchanged to vLLM.
- **Engine** (`EngineSupervisor`): lazy pool — one `vllm serve --enable-sleep-mode` per model; `load` sleeps the active worker and wakes or starts the target (`SENSERVE_SLEEP_MODE=off` falls back to kill+restart).
- **States**: `idle` → `switching` → `ready` | `error`.

## Requirements

- `GET /health` — gateway ok; `ready` when engine state is `ready`.
- `GET /v1/models` — catalog from registry (OpenAI core fields plus Senserve extensions: `name`, `source` HF repo, `status`, `loaded`, `capabilities` sorted list from registry). `status` is the pool worker's state when present; `starting` when the engine is `switching` and this model is the switch target; otherwise `cold`. `loaded: true` only on the active model while engine state is `ready`.
- `GET /ui/` — static operational dashboard (model table, worker port/pid/sleep, load/switch via admin API). No auth; intended for localhost/trusted networks only.
- `POST /v1/chat/completions` — requires `model` and `messages`; optional capability check (`SENSERVE_VALIDATE_CAPABILITIES`, default on); optional HTTP video inlining (`SENSERVE_INLINE_REMOTE_MEDIA`, default off); proxies to worker `/v1/chat/completions` (JSON or SSE stream). `file://` and `data:` video URLs pass through unchanged.
- `POST /v1/admin/models/load` — `{"model_id": "..."}` returns **202** accepted; **503** if already switching.
- `GET /v1/admin/models/status` — engine state, active/target model ids, message, error, plus `workers[]` (`model_id`, `port`, `state`, `pid`, `is_sleeping`) for each pool member.
- If requested model ≠ active: trigger background `load` (sleep active worker, wake or cold-start target) and return **503** until ready — **automatic** from Open WebUI model picker + chat; no manual vLLM `/sleep` calls.
- During `switching`: **503** + `Retry-After` (default 30, `SENSERVE_SWITCH_RETRY_AFTER_S`) + `code: model_switching`.
- Upload limit: `SENSERVE_MAX_UPLOAD_BYTES` (default 300 MiB) via `MaxBodySizeMiddleware`.
- CORS defaults include Open WebUI on `http://localhost:8788`.

## Edge cases

- Unknown or disabled model id → **404** / **400** OpenAI-style error body.
- `CapabilityError` from capability guard → **400** (disable via `SENSERVE_VALIDATE_CAPABILITIES=0`).
- `ValueError` from media size / invalid video part → **400**.
- Per-model ffmpeg frame extraction removed; vLLM ingests native `video_url` (base64 or `file://` under `allowed_local_media_path`).
- No model loaded (`--no-load`) → chat returns **503** until a model is loaded.
- Startup `--load MODEL_ID` or default from registry / `SENSERVE_DEFAULT_MODEL_ID` (`qwen3.5-0.8b`); failure exits CLI with code 1.
- Worker readiness polled up to `worker_ready_timeout_s` (default 600s) against worker `GET /v1/models`.
- `SENSERVE_SLEEP_MODE=off`: single port, kill+restart on switch (legacy).
- Per-model worker ports: `SENSERVE_WORKER_BASE_PORT + index` over enabled models sorted by id (when sleep mode on).
- During `switching`, catalog `status` for `target_model_id` is `starting` even before a worker row exists.

## Docker / Open WebUI

`compose.yaml` publishes **8787** (Senserve) and **8788** (Open WebUI). Open WebUI uses `OPENAI_API_BASE_URL=http://senserve:8787/v1`. Container command is `uv run senserve` (default model from registry `default = true`, currently `qwen3.5-0.8b`). Requires NVIDIA GPU + Container Toolkit.

Container `mem_limit` is **118g**. `HF_HOME` is `/root/.cache/huggingface`, bind-mounted from the host `~/.cache/huggingface`; `~/.cache/vllm` is mounted at `/root/.cache/vllm`; host `./datasets` is mounted read-only at `/datasets` for vLLM `allowed_local_media_path`. Compose sets `SENSERVE_INLINE_REMOTE_MEDIA=0`. Production catalog (not test fixtures) includes default `qwen3.5-0.8b`, vision `qwen3-vl-4b-awq` (AWQ weights), and `gemma-4-26b-a4b-nvfp4`.

## Runbook

### Symptoms: repeated 503 on chat with `model_switching`

- **Causes**: large model download/HF cache; vLLM warmup; prior worker still terminating.
- **Mitigation**: wait for `Retry-After`; check `GET /v1/admin/models/status` and container logs; confirm GPU memory free.
- **Prevention**: pre-load via admin endpoint or `--load` before traffic; size `start_period` healthcheck for slow first pull.

### Symptoms: worker never becomes ready

- **Causes**: wrong CUDA arch env (see `docs/dgx-vllm.md`); FlashInfer issues; OOM.
- **Mitigation**: adjust `TORCH_CUDA_ARCH_LIST` / `FLASHINFER_CUDA_ARCH_LIST`; set `VLLM_USE_FLASHINFER_SAMPLER=0` in compose.

### Symptoms: switch stays slow after first use

- **Causes**: vLLM image without sleep mode; `VLLM_SERVER_DEV_MODE` unset; wake L2 still reloading large weights from disk.
- **Mitigation**: run `uv run python scripts/verify_vllm_sleep.py` on DGX; confirm `SENSERVE_SLEEP_MODE=level2` and compose env; check `workers[].is_sleeping` in admin status.

### Symptoms: sleep/wake errors in logs (`VllmSleepError`)

- **Causes**: worker process died; admin endpoints 404; sleep called while worker not ready.
- **Mitigation**: inspect worker logs; set `SENSERVE_SLEEP_MODE=off` temporarily; kill stale processes on worker ports.

## Acceptance criteria

- `uv run pytest tests/ -q` passes.
- `GET /health` returns `ready: true` after successful model load.
- Model switch during chat yields **503** with `retry-after: 30` (`test_chat_rejects_when_switching`).
- `GET /v1/models` exposes `source`, per-model `status`, `loaded`, and `capabilities` (`test_list_models`).
- `GET /ui/` returns dashboard HTML (`test_ui_index`).
- `test_passthrough_when_inline_disabled` passes with default inline-off settings.

## Batch dataset inference

`scripts/infer_dataset_videos.py` posts `POST /v1/chat/completions` for each video under `datasets/` (gitignored). Uses `model: auto` (active model from `GET /health`), prompt *describe the video, search for any anomalies*, default `--media base64` (data URL). Optional `--media file` for `file:///datasets/<name>.mp4` when the compose datasets volume is mounted.

## Related

- Implements: [ADR-0002 — vLLM worker without Ray](../../decisions/ADR-0002-vllm-worker-no-ray.md), [ADR-0003 — Sleep mode pool](../../decisions/ADR-0003-vllm-sleep-mode-pool.md)
- Supersedes operational path from: [ADR-0001 — Single Ray LLM deployment](../../decisions/ADR-0001-single-llm-deployment.md)
