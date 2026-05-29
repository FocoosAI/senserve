---
id: ADR-0003-vllm-sleep-mode-pool
title: vLLM sleep-mode lazy worker pool
description: Lazy one vllm serve process per catalog model with Level-2 sleep/wake on switch instead of kill+restart; automatic from Open WebUI via existing load() path.
status: accepted
type: adr
domain: senserve
tags: [architecture, vllm]
related:
  - spec:SPEC-senserve-server
  - adr:ADR-0002-vllm-worker-no-ray
  - code:src/senserve/vllm_sleep.py
  - code:scripts/verify_vllm_sleep.py
anchors:
  - id: sleep-mode-default
    claim: Default sleep mode is level2
    kind: const
    file: src/senserve/settings.py
    symbol: sleep_mode
    value: '"level2"'
---

## Context

Kill+restart on every model switch re-pays vLLM process init, CUDA graphs, and JIT (30–100s+ on large models). vLLM Sleep Mode keeps processes alive and frees VRAM via `/sleep` and `/wake_up`.

## Decision

- **Lazy pool**: one `vllm serve --enable-sleep-mode` per enabled model id, port `SENSERVE_WORKER_BASE_PORT + index`.
- **Switch**: sleep active worker (L2), cold-start or wake target; orchestrated in `EngineSupervisor._switch_sleep_mode` via `vllm_sleep.py` (`wake_up?tags=weights`, `collective_rpc reload_weights`, `wake_up?tags=kv_cache`, `reset_prefix_cache`) — **not** exposed to users.
- **Verify**: `scripts/verify_vllm_sleep.py` on target image before relying on sleep in production.
- **Triggers**: unchanged — `POST /v1/chat/completions` with different `model`, admin load, CLI `--load`.
- **Fallback**: `SENSERVE_SLEEP_MODE=off` restores single-port kill+restart.
- **Compose**: `VLLM_SERVER_DEV_MODE=1` for vLLM admin endpoints on localhost only.

## Consequences

- Host RAM: one Python/CUDA process per model ever used (sleeping, not full weight RAM with L2).
- First use of each model still requires full load; benefit on repeat switches.
- Requires vLLM build with sleep mode (verify via `scripts/verify_vllm_sleep.py` on DGX).

## Related

- Refines: [ADR-0002 — vLLM worker without Ray](./ADR-0002-vllm-worker-no-ray.md)
- Operationalized by: [SPEC-senserve-server](../specs/senserve/server.md)
