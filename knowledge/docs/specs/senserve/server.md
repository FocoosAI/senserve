---
id: SPEC-senserve-server
title: Senserve server
description: Multimodal OpenAI-compatible gateway on port 8787. FastAPI proxies chat/models to a local vLLM worker (default 8000) with preprocessor hooks, single-active model hot-swap (503 + Retry-After during switch), and admin load/status endpoints.
status: accepted
type: spec
domain: senserve
tags: [senserve, architecture, runbook, vllm, openai]
related:
  - adr:ADR-0002-vllm-worker-no-ray
  - code:src/senserve/engine.py
  - code:src/senserve/gateway/openai_routes.py
  - test:tests/test_gateway_switch.py matching test_chat_rejects_when_switching
anchors:
  - id: api-port
    claim: Gateway listens on SENSERVE_API_PORT default 8787
    kind: const
    file: src/senserve/settings.py
    symbol: api_port
    value: "8787"
  - id: worker-port
    claim: vLLM worker binds SENSERVE_WORKER_PORT default 8000
    kind: const
    file: src/senserve/settings.py
    symbol: worker_port
    value: "8000"
  - id: switching-503
    claim: Model switch returns 503 with Retry-After 30
    kind: function
    file: src/senserve/gateway/errors.py
    symbol: switching_response
    signature: '(message: str = "Model switch in progress") -> JSONResponse'
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
        ▼  :8000  vllm serve (EngineSupervisor subprocess)
```

- **Registry**: `config/models.toml` (+ optional `config/models.local.toml`) defines model ids, HF sources, preprocessors, vLLM CLI options.
- **Engine** (`EngineSupervisor`): at most one worker process; `load` / `load_blocking` stop the previous `vllm serve` before starting the next.
- **States**: `idle` → `switching` → `ready` | `error`.

## Requirements

- `GET /health` — gateway ok; `ready` when engine state is `ready`.
- `GET /v1/models` — catalog from registry; `loaded: true` on the active ready model.
- `POST /v1/chat/completions` — requires `model` and `messages`; preprocesses per model spec; proxies to worker `/v1/chat/completions` (JSON or SSE stream).
- `POST /v1/admin/models/load` — `{"model_id": "..."}` returns **202** accepted; **503** if already switching.
- `GET /v1/admin/models/status` — engine state, active/target model ids, message, error.
- If requested model ≠ active: trigger background `load` and return **503** until ready.
- During `switching`: **503** + `Retry-After: 30` + `code: model_switching`.
- Upload limit: `SENSERVE_MAX_UPLOAD_BYTES` (default 300 MiB) via `MaxBodySizeMiddleware`.
- CORS defaults include Open WebUI on `http://localhost:8788`.

## Edge cases

- Unknown or disabled model id → **404** / **400** OpenAI-style error body.
- `CapabilityError` from preprocessor → **400**.
- No model loaded (`--no-load`) → chat returns **503** until a model is loaded.
- Startup `--load MODEL_ID` or default from registry / `SENSERVE_DEFAULT_MODEL_ID` (`qwen3.5-0.8b`); failure exits CLI with code 1.
- Worker readiness polled up to `worker_ready_timeout_s` (default 600s) against worker `GET /v1/models`.

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

## Acceptance criteria

- `uv run pytest tests/ -q` passes.
- `GET /health` returns `ready: true` after successful model load.
- Model switch during chat yields **503** with `retry-after: 30` (`test_chat_rejects_when_switching`).

## Related

- Implements: [ADR-0002 — vLLM worker without Ray](../../decisions/ADR-0002-vllm-worker-no-ray.md)
- Supersedes operational path from: [ADR-0001 — Single Ray LLM deployment](../../decisions/ADR-0001-single-llm-deployment.md)
