---
id: ADR-0001-single-llm-deployment
title: Single Ray LLM deployment per GPU
description: Use one Ray Serve LLMConfig and ModelManager redeploy per GPU instead of multi-model build_openai_app, so only one model occupies VRAM at a time.
status: superseded
type: adr
domain: senserve
tags: [architecture, ray]
related:
  - adr:ADR-0002-vllm-worker-no-ray
---

## Context

Early Senserve prototypes ran inference through Ray Serve LLM with vLLM as the backend engine. Operational cost (Ray cluster lifecycle, Docker complexity) motivated a simpler deployment model.

## Decision

- One `LLMConfig` / active deployment per GPU.
- Model changes via ModelManager redeploy rather than multiple concurrent OpenAI apps.

## Consequences

- Hot-swap required stopping the prior Ray deployment before loading the next model.
- Ray-specific packaging and health checks were part of the gateway operational surface.

## Superseded by

[ADR-0002 — vLLM worker subprocess without Ray](./ADR-0002-vllm-worker-no-ray.md): Ray removed; gateway spawns `vllm serve` directly via `EngineSupervisor`.
