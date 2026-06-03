---
id: IDEA-2026-06-03-active-model-vllm-reload
title: Apply vLLM flag changes without manual switch
description: When only per-model vllm options change for the active model, optionally restart that worker or document a one-click reload instead of blocking PUT with 409.
status: draft
type: idea
domain: senserve
tags: [senserve, vllm, ui]
related:
  - spec:SPEC-senserve-server
  - code:src/senserve/engine.py
created: 2026-06-03
---

# Apply vLLM flag changes without manual switch

## Context

`PUT /v1/admin/config` returns 409 if the active model's `source`, `vllm`, or `enabled` changes while `ready`. Users must switch models manually to apply flag edits.

## Sketch

- Detect vLLM-only diffs on active model → offer "Save and reload worker" (kill + cold start same id).
- Or relax 409 when only non-source vllm keys change and schedule background worker restart.

## Open questions

- Sleep-mode pool: wake after respawn or full process recycle?
- Risk of interrupting in-flight chat during reload?
