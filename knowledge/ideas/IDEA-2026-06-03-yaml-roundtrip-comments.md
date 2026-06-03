---
id: IDEA-2026-06-03-yaml-roundtrip-comments
title: Preserve YAML comments on UI save
description: Use ruamel.yaml (or similar) when writing models.yaml from the dashboard so inline comments and key order survive Save configuration.
status: draft
type: idea
domain: senserve
tags: [senserve, config, ui]
related:
  - spec:SPEC-senserve-server
  - code:src/senserve/catalog_config.py
created: 2026-06-03
---

# Preserve YAML comments on UI save

## Context

`save_config_document` uses PyYAML `safe_dump`, which strips comments from `config/models.yaml` on the first UI save.

## Sketch

- Swap to `ruamel.yaml` for write path only; keep `safe_load` or ruamel load for read.
- Optional: header comment preserved via round-trip template.

## Open questions

- Worth the dependency vs documenting that UI save reformats the file?
