---
description: One-shot knowledge update — classify a diff or pre-existing source, write/update files, regenerate INDEX. Use `--dry` to stop after classification.
argument-hint: [source] [--dry]
---

`/keep-compile` dispatches on the kind of source:

| Source kind | Examples | Behavior |
|---|---|---|
| **Code diff** | no args (working diff), `feature/auth-refresh`, `PR#142`, `main..HEAD` | Classify, propose anchors from concrete values, write/update specs+ADRs, regen INDEX. |
| **Pre-existing doc folder** | `./docs/`, `./old-docs/`, `./wiki/` | Ask: cordon the folder (default), or walk file-by-file? |
| **Pre-existing doc file** | `docs/auth/jwt.md`, `ARCHITECTURE.md` | Ask per file: migrate with verification, or cordon (default)? |

`--dry` stops after phase 1 ("preview before commit"). Source resolution when no argument: working diff first, then `git diff HEAD~1`. If ambiguous (*"compile this"* with no path), confirm before doing anything.

When a PR or branch is given, pull commit messages — rationale often hides there (*"switched from KServe because of CRD complexity"*). Quote verbatim, don't paraphrase.

---

## Phase 1 — Observe

1. **Read `INDEX.md`** to see what's already there and which `id`s exist.
2. **Detect renames**. Symbol disappears from one file and reappears under a new name with the same value (e.g. `const TOKEN_TTL = …` deleted, `const JWT_TOKEN_TTL = …` added). For each rename, check existing specs' `anchors:` blocks — if any reference the old symbol, surface a **proposed anchor update** here in phase 1. Catching renames before `/keep-check-drift` saves a CI failure.
3. **Categorize each change**: Feature (spec change), Architecture (architecture-tagged spec), Decision (new ADR), Operational (runbook-tagged spec), Refactor (no knowledge update).
4. **For each non-refactor change**, list affected knowledge files by `id` and *what* update each needs. Use `id`, not raw paths — `id` is stable, paths can move.
5. **Surface rationale-bearing commit messages verbatim** — "because", "instead of", "we tried", "incident", "rejected" are gold for ADRs and runbooks.
6. **Refactor-only diff?** Say so explicitly, propose zero updates, don't regenerate INDEX. Capturing a pure rename is the antipattern KEEP exists to prevent.

Output of phase 1:

```
Detected changes:

Feature:
- <one-line behavioral change>

Decision:
- <one-line — candidate for new ADR>

Rename detected:
- TOKEN_TTL → JWT_TOKEN_TTL in internal/auth/jwt.go
  → SPEC-auth-jwt::token_ttl anchor needs update

Suggested knowledge updates:
- [SPEC-auth-jwt] update edge cases section
- [SPEC-auth-jwt] anchor `token_ttl`: symbol rename
- [ADR-NNNN] create new ADR for <decision> (number resolved in phase 2)
```

If `--dry`, stop here.

---

## Phase 2 — Compile

For each suggested update:

- **New file**: full YAML frontmatter (see `references/file_formats.md`). `description` is a search snippet, not a chapter heading. `related` uses convention-based patterns (`code:internal/auth/*_test.go matching TestJWT*`), not hard-coded paths.
- **Updated file**: smallest possible diff. Preserve human-written rationale verbatim. Update `related` if cross-references changed.
- **Anchor update from a rename**: apply the symbol rename to the affected `anchors:` entry only. Don't invent additional anchors during this step.
- **New ADR**: `ls decisions/` first, pick the next free `ADR-NNNN`. Batch elicitation for rejected alternatives + consequences if the diff doesn't establish them. Quote commit messages that already capture rationale instead of re-asking.
- **ADR supersession**: new file gets `supersedes: [ADR-NNNN]`; old file's `status` becomes `superseded` with a `## Superseded by` section appended. Body of the old ADR is never edited.
- **New domain scaffolding**: when phase 1 introduced a domain with no prior `specs/<domain>/`, propose two companion specs — the behavioral spec for the feature that triggered the domain (e.g. `specs/billing/invoices.md`), plus an architecture-tagged stub (`specs/billing/topology.md`). Stub is fine — the point is to plant a search-snippet `description` so `/keep-ask` routes correctly. Skip the stub only when the domain is a one-file utility; note the omission.

### Anchor proposal — default behavior

When writing a new spec or substantially extending one, scan the diff for **concrete values bound to identifiers** and propose them as anchors:

- integer/float/string literals on a top-level `const`/`var` → `kind: const`
- newly added or modified function signatures → `kind: function`
- newly added `Test*` / `test_*` / `it('…')` → `kind: test`
- new HTTP routes/handlers if syntactically detectable → `kind: manual` with a `notes:` pointing to the route registration

Draft the entries directly into the spec's `anchors:` block, then ask in one batch:

> Drafted 4 anchor candidates. Reply `keep all`, `keep 1,3,4`, or `edit`.

**Rule: no anchor without evidence.** If the diff doesn't establish a binding, no anchor — the spec body documents the claim, drift doesn't enforce it. Specs without anchors still work; they sit outside the drift gate. Anchors are *enforceability*, not coverage theater.

### Brownfield — pre-existing docs

When the source is markdown (not a git diff) outside `/knowledge/`, the agent asks instead of guessing. See `references/brownfield.md` for the protocol; key points below.

**Single file**: ask `(a) migrate with verification | (b) cordon` — default `(b)`. On `(a)` → run the migrate sequence below. On `(b)` → append to an existing cordon ADR for that folder, or write a new one.

**Folder**: ask `(a) cordon whole folder with one ADR (default) | (b) walk file-by-file`. On `(a)` → write the cordon ADR (template in `references/brownfield.md`); update an existing cordon ADR for the same path instead of duplicating. On `(b)` → loop the single-file ask, with *skip* as a third option.

**Migrate sequence** (whichever way we got there) — not a verbatim copy:

1. **Classify** target type (spec / runbook-tagged spec / architecture-tagged spec / ADR / idea) per `references/brownfield.md`. Confirm.
2. **Extract claims** — verifiable (literal values, signatures, routes, test names, file refs) vs prose (rationale, history — passes through as text).
3. **Verify** each verifiable claim against current code. Mark ✓ matches / ✗ contradicts / ? unverifiable.
4. **Ask in batch** only about ?-rows and ✗-rows (max 3-4 per turn). For ✗-rows propose: drop, replace with current truth, or `<!-- TODO(KEEP): outdated -->`.
5. **Write** the spec with decisions applied. ✓ claims become text *and* anchor candidates (verification gave bindings for free). Provenance comment: `<!-- Migrated from <path> on <date>; verified against commit <sha>; claims confirmed/dropped per session with @user -->`.

### Regenerate `INDEX.md` (last step)

```bash
python <skill-path>/scripts/build_index.py knowledge/ --strict
```

Walks `/knowledge/docs/` and `/knowledge/ideas/`, parses frontmatter, emits a deterministic table-based `INDEX.md` including the auto-generated **Backlinks** section. Never hand-edit `INDEX.md` — fix the script if the layout is wrong. `--strict` exits 1 on invalid frontmatter; surface those as a `/keep-govern` backlog item.

---

## Hard rules

- Minimal diffs. If you're rewriting a paragraph, stop and reconsider.
- Frontmatter is mandatory on every file you create. Files without it are unverified artifacts.
- Verify filesystem state before sequential or set-based claims (next ADR number, whether a domain exists, whether a file is present).
- Never invent rejected alternatives, edge cases, or root causes. Use `<!-- TODO(KEEP): ... -->` for what the diff and elicitation can't establish.
- Never auto-promote idea content into specs/ADRs. Surface and ask.
- Pre-existing docs as source: ask before writing. Structured multiple-choice with a marked default. No batch migration.
- After writing, always run `build_index.py`. INDEX.md must be derived from frontmatter, including Backlinks.

## Summary output

```
Phase 1 (observe):
- Detected 3 changes: 2 features, 1 decision, 1 rename
- Suggested updates: 1 new ADR, 1 new spec, 1 spec update, 1 anchor update

Phase 2 (compile):
Created:
- [ADR-0015] knowledge/docs/decisions/ADR-0015-dual-secret-rotation.md
- [SPEC-auth-refresh] knowledge/docs/specs/auth/refresh.md (4 anchors proposed, all accepted)

Updated:
- [SPEC-auth-jwt] edge cases; anchor token_ttl symbol renamed TOKEN_TTL → JWT_TOKEN_TTL

Regenerated:
- INDEX.md (12 entries: 7 specs, 4 ADRs, 1 idea; Backlinks section refreshed)
```
