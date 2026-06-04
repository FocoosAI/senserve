---
description: Bootstrap KEEP in this repo — scaffold /knowledge, install SPEC-000-keep, append the KEEP snippet to AGENTS.md/CLAUDE.md. Run once per repo.
argument-hint: (no args)
---

Call `/keep-init` when the repo has no `/knowledge/`, when `/keep` reported "uninitialized", or when the user says *"set up KEEP here"* / *"initialize KEEP"* / *"bootstrap the knowledge layer"*.

This is a write operation — always confirm before running, even when the user clearly wants it.

## Contract

1. **Resolve the skill path** from this file's location (up two directories from `commands/keep-init.md`).

2. **Confirm with the user** in one sentence:

   > I'm about to scaffold `/knowledge/`, write `SPEC-000-keep.md`, and append the KEEP workflow snippet to `AGENTS.md` (or `CLAUDE.md` / `.cursorrules`). Source files are never modified. OK to proceed?

   If no → stop. Don't re-ask.

3. **Run the bootstrap script** from the repo root:

   ```bash
   bash <skill-path>/scripts/init.sh
   ```

   The script creates `/knowledge/docs/{specs,decisions}/`, `/knowledge/ideas/`, detects monorepo layout, appends the KEEP snippet to whichever AI entry file exists (creates `AGENTS.md` if none does), and refuses to overwrite an existing `/knowledge/`.

4. **Install `SPEC-000-keep.md`** into `/knowledge/docs/specs/keep/` from `<skill-path>/references/templates/SPEC-000-keep.md`. Copy verbatim; only update `created:` to today. The point: KEEP's own conventions become a self-spec that survives the skill being uninstalled.

5. **Regenerate the index**:

   ```bash
   python3 <skill-path>/scripts/build_index.py knowledge/
   ```

6. **Report state**:

   ```
   KEEP initialized.

   Created:
     /knowledge/{docs/{specs,decisions},ideas,INDEX.md}
     /knowledge/docs/specs/keep/SPEC-000-keep.md
   Appended KEEP snippet to: AGENTS.md

   Next: /keep-compile (current diff)  or  /keep-compile ./docs/ (pre-existing docs)
   ```

7. **Mention CI/pre-commit, don't auto-install**:

   > `/keep-check-drift` becomes a real enforcement gate when wired into CI or a pre-commit hook. Templates in `<skill-path>/references/setup.md` — ask later if you want to set that up.

## Hard rules

- Always confirm before running `init.sh`. No silent scaffold.
- Never overwrite an existing `/knowledge/`.
- Never touch `.git/`, `.github/`, or CI configuration without an explicit request — auto-installing into the user's git/CI is the kind of helpful surprise that makes tools annoying.
- Don't auto-ingest pre-existing docs after init. `/keep-compile ./docs/` is its own opt-in step.
- Idempotency: re-running on an already-initialized repo is a no-op + status print.
