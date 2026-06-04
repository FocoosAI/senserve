---
description: Periodic hygiene check — detect entropy in /knowledge over time (stale, duplicated, oversized). Suggestions only.
argument-hint: (no args)
---

Run periodically (weekly at most), not every cycle. Govern is hygiene *over time* on the whole knowledge base; `/keep-check-drift` is correctness *now* on a specific diff. A file can pass drift (matches current code) but fail govern (stale, oversized, duplicated). Both are detectors — neither modifies files.

## What to scan

Walk `/knowledge/docs/` and `/knowledge/ideas/`. Look for:

- Files with `status: accepted` not touched in >6 months where the corresponding code area has changed (use `related:` patterns to map).
- Pairs of files whose `description` or content overlap heavily — duplication candidates.
- ADRs that contradict each other without an explicit `supersedes`/`refines` link.
- Files >~300 lines that should be split.
- Files containing `<!-- TODO(KEEP): ... -->` markers — incomplete knowledge to enrich now that context may be fresher.
- Ideas in `status: draft` older than 30 days — either promote or mark `deprecated`.
- Specs without `related:` entries pointing to code/tests — the link is what makes drift detection possible.
- **Under-enforced specs — the anchor-coverage gap.** Run `scripts/coverage.py` and cross-reference with each spec's `related:`. A spec that names a code file or test in `related:` but declares no `anchors:` entry for a concrete, checkable symbol in it is *claiming* to cover code that drift cannot actually verify. Surface these as **enrich** suggestions: propose specific anchor candidates (a `const`, a function signature, a `Test*`) drawn from the already-referenced code — never anchors for symbols no spec references (that would be coverage theater). This is the loop that lifts coverage off zero over time; spec-birth anchoring alone never will.
- INDEX.md regeneration sanity: run `scripts/build_index.py --dry-run` and compare to the on-disk INDEX. If they differ, INDEX was hand-edited and needs regeneration.
- Stray directories outside the canonical layout (`/knowledge/tasks/`, `/knowledge/architecture/`, `/knowledge/runbooks/`). Canonical layout is `docs/{specs,decisions}/` + `ideas/`; runbooks/architecture live as specs with reserved tags.

## Output

Group suggestions by action — *archive*, *merge*, *summarize*, *split*, *enrich*, *collapse*, *promote*, *deprecate* — one-line rationale each. Wait for explicit per-suggestion approval before applying anything.

## Hard rules

- Suggestions only. Never auto-delete, auto-merge, or auto-rewrite.
- Preserve historical rationale. To remove a file, move it to `/knowledge/docs/_archive/` — never delete outright.
- Be conservative on staleness: a doc untouched isn't stale if the code is also untouched.
- Govern never blocks anything. For enforcement on a specific diff, use `/keep-check-drift`.
