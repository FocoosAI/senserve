---
id: IDEA-2026-06-03-admin-config-auth
title: Auth for admin config PUT
description: Protect PUT /v1/admin/config and related admin routes with a shared secret or reverse-proxy auth before exposing Senserve beyond localhost.
status: draft
type: idea
domain: senserve
tags: [senserve, security, admin]
related:
  - spec:SPEC-senserve-server
created: 2026-06-03
---

# Auth for admin config PUT

## Context

Dashboard and `PUT /v1/admin/config` can rewrite `models.yaml` with no authentication. Acceptable on localhost; risky on a LAN or misbound port.

## Sketch

- Env `SENSERVE_ADMIN_TOKEN`; require `Authorization: Bearer` on `/v1/admin/*` when set.
- Document pairing with nginx or Caddy on edge deployments.

## Open questions

- Same token for Open WebUI path or gateway-only?
- Read-only GET config without token?
