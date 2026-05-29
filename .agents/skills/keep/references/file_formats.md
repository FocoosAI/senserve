# File formats and templates

Three cross-cutting conventions — **frontmatter**, **anchors**, **linking + ADR supersession** — plus a body template per file type.

## 1. Mandatory YAML frontmatter

Every file under `/knowledge/docs/` and `/knowledge/ideas/` starts with this block:

```yaml
---
id: SPEC-auth-jwt              # stable, immutable once published
title: "JWT validation"
description: "Behavioral spec for JWT issuer/audience validation, expiry handling, and the refresh flow on /auth/* endpoints. Covers TTL, signature rotation, error cases."
status: accepted               # draft | accepted | deprecated | superseded
type: spec                     # spec | adr | idea  (runbook/architecture are tags on specs)
domain: auth                   # cohesive area; matches a top-level subdirectory under specs/
tags: [auth, jwt, security]    # free-form, lowercase, hyphens. 'runbook' and 'architecture' are reserved.
related:
  - adr:ADR-0014
  - test:internal/auth/*_test.go matching TestJWT*
  - code:internal/auth/jwt.go
---
```

Frontmatter powers retrieval (`/keep-ask` filters before opening files), drift detection (`anchors:`, see below), and INDEX regeneration (`scripts/build_index.py`). Files without it are unverified artifacts that drift and lie.

### Field semantics

- **`id`** — immutable canonical handle. ADRs: `ADR-NNNN` zero-padded. Specs: `SPEC-<domain>-<slug>`. Ideas: `IDEA-<YYYY-MM-DD>-<slug>`.
- **`title`** — short human title; used in tables and as H1.
- **`description`** — **the most load-bearing field.** It's a *search snippet*, not a chapter heading. `/keep-ask` decides whether to load a file by matching this against the user's question. Bad: *"Worker stuff"*. Good: *"Behavioral expectations for worker registration, heartbeat, job assignment, shutdown, and disconnection handling"*. Include keywords a future reader would type.
- **`status`** — `draft` (proposed, not validated), `accepted` (authoritative), `deprecated` (kept for context, team moving away), `superseded` (replaced by a specific newer file; see supersession protocol).
- **`type`** — `spec` | `adr` | `idea`. Strict vocabulary. Runbooks and architecture are tags on specs, not separate types — both are behavioral descriptions consulted the same way; splitting them would just force a routing decision on every write.
- **`domain`** — real area of the system. Monorepo: the package name (`auth-service`). Single-package: a logical area (`auth`, `billing`). Used to scope `/keep-ask` and `/keep-check-drift`.
- **`tags`** — free-form keywords. Two reserved: `runbook` (failure modes / operational), `architecture` (topology). They apply **only to `type: spec`** — they modify a spec's *role*, switching it from behavioral to operational or topological. ADRs and ideas never carry these tags; their nature is already captured by `type:`. Putting both `runbook` and `architecture` on a single spec is a smell — it claims two mutually-exclusive roles. Keep the primary one; if the file genuinely needs both, split it (one operational spec + one topology spec, linked via `related:`). INDEX groups by these tags but filters by `type: spec`.
- **`related`** — convention-based cross-references. Prefixes are mandatory: `adr:`, `spec:`, `test:`, `code:`, `runbook:`, `idea:`. For code/test, use *patterns* not hard-coded line numbers — patterns survive renames. Example: `test:internal/auth/*_test.go matching TestJWT*`.

## 2. Anchors — enforceable claims

`related:` says which files are *plausibly* affected. **Anchors** say which *values* must hold — structured claims that `scripts/check_drift.py` verifies deterministically. They live in the same frontmatter, under `anchors:`.

Anchors are opt-in per spec. A spec without anchors is valid, just outside the drift gate. Add 3-7 anchors for facts that matter, not 30.

### Schema

```yaml
anchors:
  - id: ttl
    claim: "Tokens expire after 5 minutes idle"
    kind: const                                  # const | function | test | manual
    file: services/auth/jwt.go
    symbol: TOKEN_TTL
    value: "5 * time.Minute"                     # required for kind=const

  - id: new_token_sig
    claim: "NewToken takes audience, returns (token, error)"
    kind: function
    file: services/auth/jwt.go
    symbol: NewToken
    signature: "(aud string) (string, error)"   # required for kind=function

  - id: grace_test
    claim: "Grace window allows both pre- and post-rotation secrets"
    kind: test
    file: services/auth/jwt_test.go
    symbol: TestGraceWindow_AcceptsBothSecrets

  - id: prod_cronjob
    claim: "Production rotates JWT secrets every 90 days via cronjob"
    kind: manual
    file: infra/k8s/secrets/cronjob.yaml          # not a checkable language
    notes: "Verified out-of-band via terraform state"
```

### Kinds

| kind | Required fields | What check_drift verifies |
|---|---|---|
| `const` | `file`, `symbol`, `value` | Top-level `const`/`var`/assignment of `symbol` exists with the given value (whitespace-normalized; commented declarations ignored). |
| `function` | `file`, `symbol`, `signature` | Function `symbol` declared with the given parameters and return type. Multiline and trailing commas normalized. |
| `test` | `file`, `symbol` | Test exists AND is not skipped (`t.Skip`, `@pytest.mark.skip`, `pytest.skip(...)`, `test.skip(...)`, `xtest`, `xit` all count as skipped). |
| `manual` | `file` | Nothing. Reported as known external dependency; never causes failure. |

Languages supported: Go (`.go`), Python (`.py`), TypeScript (`.ts`/`.tsx`). Other extensions surface as `missing` — either point at a checkable file or use `kind: manual`.

### Failure modes during check-drift

- **drift** — symbol found, value/signature doesn't match. User reconciles (update spec OR fix code).
- **missing** — target file or symbol absent. User restores binding OR removes anchor.
- **_validation** — invalid anchor block (missing `value` on `kind: const`, unknown `kind`, etc.). Fix the frontmatter before drift can run.

### How anchors get created

1. **By `/keep-compile`** when writing a new spec from a diff: concrete values in the diff (literals on constants, function signatures, test names) become candidate anchors. The user accepts or rejects each. This is the default path.
2. **Hand-written** during authoring, for facts the user wants enforced.

Anchors are never created speculatively. Claims the diff doesn't explicitly establish become `<!-- TODO(KEEP) -->` markers in the body, not phantom anchors.

## 3. Linking and ADR supersession

### Linking

`related:` is the machine-readable side. For prose, optionally add `## Related` at the bottom:

```md
## Related

- Implements: [ADR-0014 — Ray Serve for inference](../decisions/ADR-0014-ray-serve.md)
- Operationalized by: [specs/auth/jwt-rotation.md](./jwt-rotation.md)
```

Use relative paths. Relationship verbs (`Implements`, `Supersedes`, `Operationalized by`, `Depends on`, `See also`, `Refines`) are free-form but should be informative. When `/keep-compile` updates a file, it updates the other side of the link too (if spec A now implements ADR B, both files reference each other).

### ADR supersession protocol

When ADR-MMMM supersedes ADR-NNNN:

1. **New ADR (MMMM)** with `status: accepted`. In `related:` add `adr:ADR-NNNN` with a `supersedes` note. In `## Context`, explain what changed.
2. **Old ADR (NNNN)** updated in place — *two changes only*: `status: superseded` in frontmatter, and a `## Superseded by` section at the end with a one-line summary and link to MMMM. **Never edit the body of a superseded ADR** — it's a historical record.
3. **INDEX.md** regenerates automatically; superseded entries move to a dedicated section.

### Partial supersession (refinement)

When a new ADR doesn't fully replace an old one but modifies a specific aspect, use `Refines:` in `## Related` and keep the old ADR's status `accepted`. The old ADR adds a `## Refined by` section pointing to the newer file. Don't conflate refinement with supersession — it loses context.

### Translating user statements about status

| User says | Status |
|---|---|
| "still accepted" / "still current" | `accepted` |
| "this has been replaced" / "we don't do this anymore" | `superseded` (full supersession protocol) |
| "partially superseded" / "we updated part of this" | `accepted` + `Refines:` link from the newer ADR |
| "deprecated" / "we're moving away from this" | `deprecated` |
| "we never really followed this" / "this was aspirational" | `deprecated` |

If the user names the successor, capture it. If they don't, don't fabricate — leave `<!-- TODO(KEEP): successor decision not identified -->`.

---

## Body templates

### Spec (behavioral)

```md
---
id: SPEC-auth-jwt
title: "JWT validation"
description: "..."
status: accepted
type: spec
domain: auth
tags: [auth, jwt, security]
related: [adr:ADR-0014, test:internal/auth/*_test.go matching TestJWT*, code:internal/auth/jwt.go]
---

# JWT validation

## Goal
<one or two sentences>

## Requirements
- <requirement>

## Edge cases
- <case>

## Acceptance criteria
- <verifiable criterion>
```

Omit empty sections.

### Spec with `runbook` tag (operational)

Describes failure modes and operational response. Same frontmatter shape; body uses different sections:

```md
# JWT secret rotation

## Symptoms
- <observable signal>

## Causes
- <root cause>

## Mitigation
- <step>

## Prevention
- <change that would prevent recurrence>
```

Only write a runbook for a failure that has actually happened or is genuinely likely. Speculative runbooks become noise.

### Spec with `architecture` tag (topology / boundaries)

```md
# Auth service architecture

## Components
- <component>: <one-line role>

## Flow
<text or ASCII diagram>

## Boundaries
- <what is inside>
- <what is intentionally outside>

## Constraints
- <constraint>
```

ASCII diagrams are fine and often more durable than image links:

```
Frontend → API Gateway → Auth Service → Postgres
                              ↓
                         JWT (HS256)
```

### ADR

```md
---
id: ADR-0014
title: "Ray Serve for ML inference"
description: "Adopted Ray Serve. KServe rejected (CRD complexity). Custom FastAPI rejected (undifferentiated reinvention)."
status: accepted
type: adr
domain: inference
tags: [inference, infrastructure, ml]
related: [adr:ADR-0007, spec:SPEC-inference-pipeline, code:services/inference/server.go]
---

# ADR-0014: Ray Serve for ML inference

## Status
Accepted

## Context
<the problem or constraint that forced a choice>

## Decision
<what was chosen, in one or two sentences>

## Alternatives considered
### <alternative 1>
- <why rejected>

### <alternative 2>
- <why rejected>

## Consequences
- <tradeoff>

## Drivers
<positive factors that made this option win on its own merits — distinct from rejected-alternatives reasoning>
- <factor>
```

**ADR numbering**: sequential, never reused. Before assigning a number, list `decisions/` and use the next available integer — never pre-number from memory or the conversation flow.

**The three rationale sections** answer different questions and shouldn't be merged:

- **Alternatives considered** — *"why didn't we pick X?"* — one entry per rejected option.
- **Consequences** — *"what cost are we accepting?"* — tradeoffs, downsides, future obligations.
- **Drivers** — *"why this option specifically?"* — positive factors.

The separation is what makes ADRs useful in a year when someone asks *"what were we thinking?"*.

### Idea

```md
---
id: IDEA-2026-05-13-jwt-grace-window
title: "Grace window for JWT secret rotation"
description: "Idea: introduce a grace window where both current and previous JWT secrets validate, to avoid the 401 spike at deploy time."
status: draft
type: idea
domain: auth
tags: [auth, jwt, rotation, brainstorm]
related: [runbook:SPEC-auth-jwt-rotation]
created: 2026-05-13
---

# Grace window for JWT secret rotation

## Context
<why this came up, what triggered the idea>

## Sketch
<the rough proposal — not a final design>

## Open questions
- <question to resolve before promoting>
```

Status flow: `draft` → promoted via `/keep-compile` (status becomes `deprecated`, `related:` points to the resulting spec/ADR) OR dropped (status: `deprecated` with a one-line note). `/keep-govern` flags ideas older than 30 days still in `draft`.

---

## INDEX.md — auto-generated

`scripts/build_index.py` walks `/knowledge/docs/` and `/knowledge/ideas/`, parses frontmatter, emits a deterministic table-based INDEX with one section per `type`, dedicated sections for `runbook`/`architecture` tags, a `Backlinks` section (reverse map of `related:`), a superseded section, and a "frontmatter issues" section for files missing required fields. `/keep-compile` calls the script as its last step.

Never hand-edit INDEX.md — if the layout is wrong, fix the script. Hand-editing reintroduces the drift this system exists to prevent.
