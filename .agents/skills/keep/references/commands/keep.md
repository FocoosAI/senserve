---
description: KEEP root command — shows /knowledge status as a dashboard, or points to /keep-init if missing. Run with no args.
argument-hint: (no args)
---

Entry point — *"see what's going on with KEEP in this repo"* or *"I don't remember which command"*. Read-only by contract.

## Contract

1. **Run the status script** from the repo root (skill path is two levels up from this file):

   ```bash
   python3 <skill-path>/scripts/status.py
   ```

2. **Branch on exit code:**

   - **Exit 0** — `/knowledge/` exists. Show the script's output verbatim, then the command map below. If the status output flags **low code-level anchor coverage**, don't leave it as a passive number: offer to act. Say something like *"coverage is low — want me to find the high-value gaps?"* and, on yes, run `scripts/coverage.py`, cross-reference each spec's `related:`, and propose anchoring the symbols a spec already references but doesn't enforce (the non-theater candidates — see `/keep-govern`). The dashboard is the natural place to start closing the gap, not just report it.

   - **Exit 2** — `/knowledge/` missing. Tell the user:

     > KEEP is not initialized in this repo. Run `/keep-init` to scaffold `/knowledge/`, install `SPEC-000-keep.md`, and append the KEEP snippet to `AGENTS.md`. It will ask for confirmation before writing anything.

     Do NOT silently run `/keep-init` — `/keep` is read-only. If the user seems unsure whether KEEP is right for the repo, give the two-sentence pitch (*a living knowledge layer — specs, ADRs, ideas, with anchored facts that drift-check catches deterministically*) and let them decide.

3. **Append the command map**:

   ```
   /keep-ask <question>   → read /knowledge with citations
   /keep-init             → bootstrap (one-time per repo)
   /keep-compile [--dry]  → classify diff/source, write/update specs/ADRs, regen INDEX
   /keep-check-drift      → verify anchors against current code (CI-friendly, exit 1 on drift)
   /keep-idea <thought>   → capture a parked idea durably
   /keep-govern           → periodic hygiene (run weekly)
   ```

## Hard rules

- Never modify `/knowledge/`. Status or routing only.
- Don't bootstrap silently. Init requires explicit user invocation of `/keep-init`.
- Resolve the skill path from this file's location. Hardcoded paths break portability.
- If `status.py` errors with anything other than exit 2 (e.g. permissions), surface the error verbatim and stop — don't try to fix it.
