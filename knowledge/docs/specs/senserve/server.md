---
id: SPEC-senserve-server
title: Senserve server
description: Multimodal OpenAI-compatible gateway on port 8787. FastAPI proxies chat/models to a lazy vLLM worker pool (sleep mode L2, base port 8000) with automatic sleep/wake on model switch, 503 + Retry-After, and admin load/status.
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
  - test:tests/test_gateway_switch.py matching test_chat_rejects_when_switching
  - test:tests/test_engine_sleep.py matching test_vllm_cmd_includes_sleep_flag
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
---

# Senserve server

## Goal

Expose a stable OpenAI-compatible HTTP API that routes multimodal chat to whichever vLLM model is active, loading models on demand without Ray.

## Architecture

```
Client / Open WebUI
        │
        ▼  :8787  FastAPI (senserve.gateway)
        │         preprocessors → proxy
        ▼  :8000+N  vllm serve pool (sleep/wake per model)
```

- **Registry**: `config/models.toml` (+ optional `config/models.local.toml`) defines model ids, HF sources, preprocessors, vLLM CLI options.
- **Engine** (`EngineSupervisor`): lazy pool — one `vllm serve --enable-sleep-mode` per model; `load` sleeps the active worker and wakes or starts the target (`SENSERVE_SLEEP_MODE=off` falls back to kill+restart).
- **States**: `idle` → `switching` → `ready` | `error`.

## Requirements

- `GET /health` — gateway ok; `ready` when engine state is `ready`.
- `GET /v1/models` — catalog from registry; `loaded: true` on the active ready model.
- `POST /v1/chat/completions` — requires `model` and `messages`; preprocesses per model spec; proxies to worker `/v1/chat/completions` (JSON or SSE stream).
- `POST /v1/admin/models/load` — `{"model_id": "..."}` returns **202** accepted; **503** if already switching.
- `GET /v1/admin/models/status` — engine state, active/target model ids, message, error, plus `workers[]` (`model_id`, `port`, `state`, `pid`, `is_sleeping`) for each pool member.
- If requested model ≠ active: trigger background `load` (sleep active worker, wake or cold-start target) and return **503** until ready — **automatic** from Open WebUI model picker + chat; no manual vLLM `/sleep` calls.
- During `switching`: **503** + `Retry-After` (default 30, `SENSERVE_SWITCH_RETRY_AFTER_S`) + `code: model_switching`.
- Upload limit: `SENSERVE_MAX_UPLOAD_BYTES` (default 300 MiB) via `MaxBodySizeMiddleware`.
- CORS defaults include Open WebUI on `http://localhost:8788`.

## Edge cases

- Unknown or disabled model id → **404** / **400** OpenAI-style error body.
- `CapabilityError` from preprocessor → **400**.
- No model loaded (`--no-load`) → chat returns **503** until a model is loaded.
- Startup `--load MODEL_ID` or default from registry / `SENSERVE_DEFAULT_MODEL_ID` (`qwen3.5-0.8b`); failure exits CLI with code 1.
- Worker readiness polled up to `worker_ready_timeout_s` (default 600s) against worker `GET /v1/models`.
- `SENSERVE_SLEEP_MODE=off`: single port, kill+restart on switch (legacy).
- Per-model worker ports: `SENSERVE_WORKER_BASE_PORT + index` over enabled models sorted by id (when sleep mode on).

## Docker / Open WebUI

`compose.yaml` publishes **8787** (Senserve) and **8788** (Open WebUI). Open WebUI uses `OPENAI_API_BASE_URL=http://senserve:8787/v1`. Default container command loads `qwen3.5-0.8b`. Requires NVIDIA GPU + Container Toolkit.

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

## Related

- Implements: [ADR-0002 — vLLM worker without Ray](../../decisions/ADR-0002-vllm-worker-no-ray.md), [ADR-0003 — Sleep mode pool](../../decisions/ADR-0003-vllm-sleep-mode-pool.md)
- Supersedes operational path from: [ADR-0001 — Single Ray LLM deployment](../../decisions/ADR-0001-single-llm-deployment.md)
