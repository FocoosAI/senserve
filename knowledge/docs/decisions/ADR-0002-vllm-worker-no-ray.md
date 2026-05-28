---
id: ADR-0002-vllm-worker-no-ray
title: vLLM worker subprocess without Ray
description: Replace Ray Serve LLM with a local vllm serve subprocess per active model; Senserve FastAPI gateway proxies OpenAI traffic after preprocessing.
status: accepted
type: adr
domain: senserve
tags: [architecture, vllm]
related:
  - spec:SPEC-senserve-server
supersedes:
  - adr:ADR-0001-single-llm-deployment
---

## Context

Ray added operational weight and Docker friction. vLLM already ships an OpenAI-compatible server.

## Decision

- **Gateway** (`senserve.gateway`): FastAPI on port 8787, unchanged API surface.
- **Engine** (`senserve.engine.EngineSupervisor`): spawns `vllm serve` on `SENSERVE_WORKER_PORT` (default 8000).
- **A1**: one active worker; load stops the previous process. Policy hooks reserved for concurrent/LRU later.
- **Registry**: `config/models.toml` (+ optional `models.local.toml`).

## Consequences

- Multi-model concurrent (A2) = multiple worker ports + supervisor policy (not yet implemented).
- GPU required for model load; gateway runs without GPU when `--no-load`.
