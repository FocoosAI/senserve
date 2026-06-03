---
id: ADR-0002-vllm-worker-no-ray
title: vLLM worker subprocess without Ray
description: Replace Ray with local vllm serve subprocesses (one active in VRAM; lazy pool and sleep/wake per ADR-0003). FastAPI gateway on 8787 proxies OpenAI chat with optional capability checks and HTTP video inlining.
status: accepted
type: adr
domain: senserve
tags: [architecture, vllm]
related:
  - spec:SPEC-senserve-server
  - adr:ADR-0001-single-llm-deployment
  - adr:ADR-0003-vllm-sleep-mode-pool
supersedes:
  - adr:ADR-0001-single-llm-deployment
anchors:
  - id: engine-load
    claim: EngineSupervisor.load orchestrates model switch (sleep/wake pool or kill+restart when sleep off)
    kind: function
    file: src/senserve/engine.py
    symbol: load
    signature: "(self, model_id: str) -> None"
---

## Context

Ray added operational weight and Docker friction. vLLM already ships an OpenAI-compatible server.

## Decision

- **Gateway** (`senserve.gateway`): FastAPI on port 8787, unchanged OpenAI API surface.
- **Engine** (`senserve.engine.EngineSupervisor`): spawns `vllm serve` per catalog model (see ADR-0003 for sleep pool and port layout).
- **A1**: one active model in VRAM at a time; switch sleeps or stops the prior worker. Policy hooks reserved for concurrent/LRU later.
- **Registry**: `config/models.yaml` (+ optional `models.local.yaml`).

## Consequences

- Multi-model concurrent (A2) = multiple worker ports + supervisor policy (not yet implemented).
- GPU required for model load; gateway runs without GPU when `--no-load`.

## Refined by

[ADR-0003 — vLLM sleep-mode lazy worker pool](./ADR-0003-vllm-sleep-mode-pool.md): default switch uses sleep Level 2 and a lazy pool (one `vllm serve --enable-sleep-mode` per catalog model, ports `SENSERVE_WORKER_BASE_PORT + index`) instead of terminating the previous process. `SENSERVE_SLEEP_MODE=off` preserves the kill+restart behavior described above on a single port.
