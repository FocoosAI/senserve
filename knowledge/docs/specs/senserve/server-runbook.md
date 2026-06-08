---
id: SPEC-senserve-server-runbook
title: Senserve server runbook
description: Runbook for Senserve gateway — 503 during starting/switching, gateway up before ready, stuck load cancel, worker exit fail-fast, sleep/wake, config 409, vLLM flags preload.
status: accepted
type: spec
domain: senserve
tags: [senserve, runbook, vllm]
related:
  - spec:SPEC-senserve-server
  - adr:ADR-0003-vllm-sleep-mode-pool
---

# Senserve server runbook

## Symptoms: gateway/UI up but chat returns 503 / `ready: false`

- **Causes**: normal during engine `starting` (first default model load) or `switching`; vLLM still warming on GPU.
- **Mitigation**: poll `GET /health` until `engine: ready` and `ready: true`; watch `GET /v1/admin/models/status` and dashboard Workers table.
- **Note**: `/health` returns **200** while not ready — distinguish gateway liveness from model readiness.

## Symptoms: repeated 503 on chat with `model_switching`

- **Causes**: large model download/HF cache; vLLM warmup; prior worker still terminating.
- **Mitigation**: wait for `Retry-After`; check `GET /v1/admin/models/status` and container logs; confirm GPU memory free.
- **Prevention**: pre-load via admin endpoint or `--load` before traffic; size `start_period` healthcheck for slow first pull.

## Symptoms: engine stuck in `starting` or `switching` with `pid: null` / target worker dead

- **Causes**: vLLM subprocess exited during startup (CUDA/NVML, OOM); prior behavior waited up to `worker_ready_timeout_s` (~600s) on HTTP poll only.
- **Mitigation**: use **Cancel switch** in the dashboard or `POST /v1/admin/models/cancel` to kill the target worker and restore the previous active model; check `docker compose logs senserve` and `nvidia-smi` inside the container; restart the service if the GPU driver is wedged.
- **Prevention**: engine fails fast when the worker is dead **and** HTTP `/v1/models` is unreachable; if the vLLM launcher parent exits but APIServer still serves the port, the switch continues (avoids false "exited before ready" on large models).

## Symptoms: worker never becomes ready

- **Causes**: wrong CUDA arch env (see `docs/dgx-vllm.md`); FlashInfer issues; OOM.
- **Mitigation**: adjust `TORCH_CUDA_ARCH_LIST` / `FLASHINFER_CUDA_ARCH_LIST`; set `VLLM_USE_FLASHINFER_SAMPLER=0` in compose.

## Symptoms: switch stays slow after first use

- **Causes**: vLLM image without sleep mode; `VLLM_SERVER_DEV_MODE` unset; wake L2 still reloading large weights from disk.
- **Mitigation**: run `uv run python scripts/verify_vllm_sleep.py` on DGX; confirm `SENSERVE_SLEEP_MODE=level2` and compose env; check `workers[].is_sleeping` in admin status.

## Symptoms: sleep/wake errors in logs (`VllmSleepError`)

- **Causes**: worker process died; admin endpoints 404; sleep called while worker not ready; **sleep HTTP timed out** while vLLM waits for in-flight chat (Open WebUI streams on the active model).
- **Mitigation**: stop client traffic to the active model, then retry switch; use **Cancel switch** (`POST /v1/admin/models/cancel`) — cancel interrupts sleep within ~5s and restores the prior active model; on sleep timeout the engine **kills** the active worker and continues the switch instead of staying stuck for 120s; raise `SENSERVE_WORKER_SLEEP_TIMEOUT_S` (default 600, same order as ready timeout) for very large models; set `SENSERVE_SLEEP_MODE=off` temporarily; inspect worker logs and stale processes on worker ports.

## Symptoms: catalog save returns 409 from dashboard

- **Causes**: engine `starting` or `switching`; or edit changes `source` / per-model `vllm` / `enabled` on the **active** model while `ready`.
- **Mitigation**: wait for load/switch to finish; load another model first, or edit only non-active entries; unload via switch before changing active model flags.

## Symptoms: vLLM autocomplete empty in Configuration UI

- **Causes**: `preload_at_startup()` failed at boot (vLLm missing on PATH) so `GET /v1/admin/vllm/flags` returns 503; or config panel opened before lifespan preload on non-CLI entrypoints.
- **Mitigation**: check startup logs for "Preloaded N vLLM serve flags"; ensure vLLM is in the image; `GET /v1/admin/vllm/flags?refresh=1` after upgrading vLLM; manual key entry still works.

## Related

- Behavior and API: [SPEC-senserve-server](./server.md)
