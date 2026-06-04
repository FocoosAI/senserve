---
description: Detect drift between code and /knowledge/ — deterministic anchor verification, CI-friendly (exit 1 on drift).
argument-hint: [--spec SPEC-id] [--changed] [--verbose]
---

The enforcement gate. Every PR, every pre-merge. Answers a precise question:

> *Do the anchors declared in spec frontmatter still hold against the current code?*

Backed by `scripts/check_drift.py` — deterministic, stdlib-only Python. No LLM in the loop. Exit 1 blocks merge.

## How it works

For each anchor in spec frontmatter under `/knowledge/`, the script reads the target file (Go, Python, TypeScript) and applies a language-specific regex matcher per kind:

- **const** — `const X = …` declaration matches expected value (whitespace-tolerant)
- **function** — signature equality (params + return)
- **test** — test exists AND is not skipped (`t.Skip`, `@pytest.mark.skip`, `test.skip(...)`, `xtest`, etc. all count as skipped)
- **manual** — reported but never causes failure (external verification)

## Invocation

```bash
python3 <skill-path>/scripts/check_drift.py [flags]
```

When asked to "check for drift", run this script — don't re-implement the matching with an LLM.

Flags:

- `--knowledge <path>` (default `./knowledge`)
- `--repo <path>` (default the parent of `--knowledge`)
- `--spec SPEC-id` — limit to a single spec
- `--changed` — only check anchors whose target file appears in `git diff` (CI/pre-commit shortcut)
- `--verbose` — show OK results too

Exit codes: `0` no drift, `1` drift or missing target, `2` usage error.

## Wiring as a hook

`/keep-check-drift` becomes a real enforcement gate only when wired into CI or pre-commit — exit 1 then blocks the PR/commit until the spec is updated or the code change reverted. The copy-paste templates (a `$SKILL_PATH`-parameterized pre-commit hook and a GitHub Action that clones KEEP into the runner) live in `references/setup.md`. Use those rather than hardcoding a path here: the script's location depends on where KEEP is installed, and a literal `skills/KEEP/scripts/…` only resolves inside the KEEP source repo.

## What this is NOT

- **Not a fixer.** The detector does not know whether the spec or the code is wrong. The user decides.
- **Not opinionated about prose.** Only `anchors:` frontmatter entries are checked. Body prose is not parsed.
- **Not an LLM call.** Pure regex, <30 ms for hundreds of anchors, reproducible.

## Hard rules

- No file writes. Detection only.
- Specs without `anchors:` produce neither warnings nor failures — anchoring is opt-in per spec.
- A purely mechanical refactor that doesn't change any anchored value/signature produces zero findings.
- `missing` (target file gone) is drift — either restore the file or remove the anchor.
