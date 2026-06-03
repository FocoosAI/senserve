---
id: SPEC-senserve-server-runbook
title: Senserve server runbook
description: Operational symptoms and mitigations for the Senserve gateway and vLLM worker pool — 503 switching, readiness, sleep/wake, catalog save conflicts, UI vLLM flags.
status: accepted
type: spec
domain: senserve
tags: [senserve, runbook, vllm]
related:
  - spec:SPEC-senserve-server
  - adr:ADR-0003-vllm-sleep-mode-pool
---

# Senserve server runbook

## Symptoms: repeated 503 on chat with `model_switching`

- **Causes**: large model download/HF cache; vLLM warmup; prior worker still terminating.
- **Mitigation**: wait for `Retry-After`; check `GET /v1/admin/models/status` and container logs; confirm GPU memory free.
- **Prevention**: pre-load via admin endpoint or `--load` before traffic; size `start_period` healthcheck for slow first pull.

## Symptoms: worker never becomes ready

- **Causes**: wrong CUDA arch env (see `docs/dgx-vllm.md`); FlashInfer issues; OOM.
- **Mitigation**: adjust `TORCH_CUDA_ARCH_LIST` / `FLASHINFER_CUDA_ARCH_LIST`; set `VLLM_USE_FLASHINFER_SAMPLER=0` in compose.

## Symptoms: switch stays slow after first use

- **Causes**: vLLM image without sleep mode; `VLLM_SERVER_DEV_MODE` unset; wake L2 still reloading large weights from disk.
- **Mitigation**: run `uv run python scripts/verify_vllm_sleep.py` on DGX; confirm `SENSERVE_SLEEP_MODE=level2` and compose env; check `workers[].is_sleeping` in admin status.

## Symptoms: sleep/wake errors in logs (`VllmSleepError`)

- **Causes**: worker process died; admin endpoints 404; sleep called while worker not ready.
- **Mitigation**: inspect worker logs; set `SENSERVE_SLEEP_MODE=off` temporarily; kill stale processes on worker ports.

## Symptoms: catalog save returns 409 from dashboard

- **Causes**: engine `switching`; or edit changes `source` / per-model `vllm` / `enabled` on the **active** model while `ready`.
- **Mitigation**: wait for switch to finish; load another model first, or edit only non-active entries; unload via switch before changing active model flags.

## Symptoms: vLLM autocomplete empty in Configuration UI

- **Causes**: `GET /v1/admin/vllm/flags` returns 503 when `vllm` is not on PATH inside the container.
- **Mitigation**: ensure vLLM is installed in the image; use `?refresh=1` after upgrading vLLM; manual key entry still works.

## Related

- Behavior and API: [SPEC-senserve-server](./server.md)
