# Brownfield — handling pre-existing documentation

Read this when `/keep-compile` is given a folder or file of pre-existing docs (anywhere outside `/knowledge/`), or when `/keep-init` runs on a repo with existing docs.

A stale spec is worse than a missing one: once it sits in `/knowledge/` carrying the implicit *"this is current truth"*, it poisons every `/keep-ask` and misleads every reader. KEEP only promotes a doc to authoritative *after* somebody confirms it's still true.

## Default behavior: ask

The agent never writes silently from pre-existing docs. It asks with structured multiple-choice and a safe default.

### Single file source — `/keep-compile docs/auth/jwt.md`

1. Read title + first paragraph so the question is specific.
2. Ask:

   > `docs/auth/jwt.md` looks like an existing doc.
   >
   > **(a) Migrate** — extract claims, verify each against current code, ask about disagreements/unbindings, write a clean spec into `/knowledge/`. Verified literal values become anchors.
   >
   > **(b) Cordon** — mark legacy / out of scope for `/keep-ask` and `/keep-check-drift`. File stays put.   ← default

3. **(a)** → migrate sequence below.
4. **(b)** or silence → append path to existing cordon ADR if one covers this folder, else write a new one referencing this single file.

### Folder source — `/keep-compile ./docs/`

1. Walk the folder once to count files.
2. Ask:

   > `./docs/` has 12 markdown files.
   >
   > **(a) Cordon the folder** — one ADR declares the whole folder legacy. Files stay put. Fast, safe.   ← default
   >
   > **(b) Walk file-by-file** — ask per file: migrate, cordon, or skip. Use when a few docs are genuinely current.

3. **(a)** or silence → write cordon ADR (template below).
4. **(b)** → loop the single-file ask per file. *Skip* leaves the file untouched and out of the cordon list.

Cordon is the safer default: KEEP would rather admit ignorance than masquerade unverified prose as truth. Migration is per-file because an operator can't realistically verify 10+ legacy docs in one session.

## Migrate sequence

Not a verbatim copy. Five steps:

1. **Classify** — propose target type from headings/body (table below). Confirm.

2. **Extract claims** — scan for *verifiable* claims:
   - literal values bound to identifiers (`TOKEN_TTL = 5min`, batch size 4)
   - function/method signatures
   - endpoints/routes
   - test names
   - file path references

   Prose (rationale, alternatives, history) passes through as text — no binding, but belongs in the spec.

3. **Verify** — grep each verifiable claim against current code:
   - ✓ **matches** — consistent with code
   - ✗ **contradicts** — disagrees with code
   - ? **unverifiable** — no code binding found

4. **Ask** — present a verification table; ask only about ?-rows and ✗-rows. Batch 3-4 per turn.

   ```
   docs/auth/jwt.md — verification:

   ✓ TOKEN_TTL = 5min                    matches const TOKEN_TTL in internal/auth/jwt.go
   ✓ ValidateToken(token, secret, iss)   matches signature
   ✗ uses HS256 signing                  code uses ES256 — outdated
   ? GET /auth/refresh returns 200       no obvious route binding — confirm or drop?
   ? "rate limit 100 req/min"            no code binding — confirm or drop?
   ```

   For ✗: drop / replace with current truth / keep with `<!-- TODO(KEEP): outdated -->`. Never ask about ✓.

5. **Write** the spec with decisions applied:
   - ✓ → text in body + anchor entries (verification gave bindings for free).
   - ✗ → dropped or rewritten per choice.
   - ? → dropped or kept per choice; kept ones may get `kind: manual` anchors if a non-code source exists.
   - Provenance comment: `<!-- Migrated from docs/auth/jwt.md on <YYYY-MM-DD>; verified against commit <sha> -->`

Verification is the heart of the protocol — every load-bearing claim is individually re-validated, accepted, rewritten, or marked uncertain.

## Cordon ADR template

```md
---
id: ADR-NNNN
title: "Legacy documentation cordoned off"
description: "Pre-existing documentation in <path> is declared legacy and out of scope for /keep-ask, /keep-check-drift, and INDEX. Retained for historical reference."
status: accepted
type: adr
domain: keep
tags: [keep, brownfield, migration]
related:
  - code:<path>
---

# ADR-NNNN: Legacy documentation cordoned off

## Status
Accepted

## Context

Repository adopted KEEP on <YYYY-MM-DD>. Pre-existing docs in `<path>` may describe an earlier state. The team has not validated each file against current code; unverified prose should not become authoritative in `/keep-ask`.

## Decision

`<path>` is **legacy / out of scope** for KEEP:

- `/keep-ask` does NOT load files from `<path>`.
- `/keep-check-drift` does NOT verify anchors against `<path>`.
- `INDEX.md` does NOT list `<path>` files.
- Folder retained unchanged for context.

## Alternatives considered

### Migrate all with per-file approval
Rejected: each ingested file carries an implicit currency claim impossible to back at scale.

### Delete the legacy folder
Rejected: historical context has value; deletion is irreversible; specific files can still be migrated later.

## Consequences

- New behavior is documented in `/knowledge/` going forward.
- Specific legacy files can be migrated later — the per-file ask kicks in.
- `/keep-ask` may return *"no indexed knowledge"* on topics only covered by the legacy folder. The agent says so honestly.

## Files cordoned (snapshot at adoption)

- `<path>/ARCHITECTURE.md`
- `<path>/runbooks/jwt-rotation.md`
- ...
```

Snapshot at adoption. Files added later are legacy by inheritance unless explicitly migrated. If a cordon ADR already covers the path, **update it** — don't write a duplicate.

## Classification heuristics

When migrating, pick target type from structure + keywords. When they disagree, prefer structure.

| Target | Heading patterns | Keyword signals | Output path |
|---|---|---|---|
| **spec** (behavior) | `## Endpoint`, `## API`, `## Behavior`, `## Requirements`, `## Acceptance criteria`, `## Edge cases`, `## Errors` | "shall", "must return", "given/when/then", "validates", "returns 4xx/5xx" | `specs/<package>/<slug>.md` |
| **spec + `runbook`** | `## Symptoms`, `## Detection`, `## Cause(s)`, `## Mitigation`, `## Rollback`, `## Alerts` | "alert", "paged", "on-call", "incident", "RTO/RPO", "5xx spike", "OOMKilled" | `specs/<package>/<slug>.md`, `tags: [<domain>, runbook]` |
| **spec + `architecture`** | `## Topology`, `## Components`, `## Boundaries`, `## Data flow`, `## Sequence`, `## Diagram` | "service", "boundary", "talks to", "depends on", ASCII/mermaid diagrams | `specs/<package>/topology.md`, `tags: [<domain>, architecture]` |
| **ADR** | `## Status`, `## Context`, `## Decision`, `## Consequences`, `## Alternatives`, `## Drivers` | "we chose X because", "rejected Y", "tradeoff", explicit alternatives | `decisions/ADR-NNNN-<slug>.md` |
| **idea** | `## Proposal`, `## Idea`, `## Thinking`, `## RFC draft` | "we should", "what if", "thinking about", "to investigate" | `ideas/<YYYY-MM-DD>-<slug>.md`, `status: draft` |
| **mixed / unclear** | Multiple patterns OR thin/boilerplate | — | Ask to split or skip; never lump |

Infer `<package>` from source path: `services/auth/docs/jwt.md` → `specs/auth/jwt.md`.

## Files never migrated

Skip with a one-line note:

- `CHANGELOG.md` — derived from git history
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` — process docs, not system knowledge
- Auto-generated API references — derived artifact
- `LICENSE`, `NOTICE` — legal text

## Where to scan during adoption

Walk recursively, excluding `node_modules`, `.git`, `dist`, `build`, `target`, `vendor`, `.venv`, `/knowledge/`:

- `README.md` at root and per package
- `docs/`, `doc/`, `documentation/`
- `ARCHITECTURE.md`, `DESIGN.md`, equivalents as directories
- `RUNBOOK.md`, `runbooks/`
- `notes/`, `rfc/`, `rfcs/`, `adr/`, `decisions/`
- `wiki/`, `.wiki/`
- Top-level `*.md` excluding boilerplate

## Hard rules

- Never write against pre-existing docs without asking. Multiple-choice with a marked default.
- Source files are read-only. KEEP never modifies, moves, or deletes them.
- `/keep-ask` and `/keep-check-drift` ignore everything under a cordoned path.
- Re-running `/keep-compile <same-folder>` after cordon updates the existing ADR; never writes a duplicate.
- Migration is per-file. No batch migration. The friction is the feature.
