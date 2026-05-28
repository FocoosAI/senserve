# Setup — adoption guide

First-time adoption: canonical AGENTS.md snippet, common mistakes, how to verify the loop, and how to wire `/keep-check-drift` into CI or pre-commit.

## Step 1 — initialize

Use **`/keep-init`** — a slash command that:

1. Asks for explicit consent.
2. Runs `scripts/init.sh` to scaffold `/knowledge/docs/{specs,decisions}/`, `/knowledge/ideas/`, `INDEX.md`. Detects monorepo shape. Appends the KEEP snippet to `AGENTS.md` (or `CLAUDE.md` / `.cursorrules` if either exists).
3. Installs `SPEC-000-keep.md` into `/knowledge/docs/specs/keep/` — a self-spec so KEEP's conventions survive the skill being uninstalled.
4. Runs `scripts/build_index.py`.

Manual equivalent:

```bash
bash <skill-path>/scripts/init.sh
cp <skill-path>/references/templates/SPEC-000-keep.md knowledge/docs/specs/keep/
python3 <skill-path>/scripts/build_index.py knowledge/
```

`init.sh` refuses to overwrite an existing `/knowledge/`. Re-running `/keep-init` on an initialized repo is a no-op + status print.

Empty subdirectories are fine. Do NOT backfill specs/ADRs for existing code — the first real updates come from the next meaningful change via `/keep-compile`.

### Monorepo

`init.sh` auto-detects (`pnpm-workspace.yaml`, `go.work`, `Cargo.toml`, top-level `apps/` `packages/` `services/`). Per-package subdirs under `specs/` are created lazily on first `/keep-compile`.

### Pre-existing docs — the agent asks

The agent never silently imports existing docs. Point `/keep-compile` at them and answer the prompt:

```
/keep-compile ./docs/              ← folder → cordon or walk file-by-file?
/keep-compile docs/auth/jwt.md     ← file   → migrate (with verification) or cordon?
```

Default is cordon — one ADR declares the source out of scope for `/keep-ask` and `/keep-check-drift`. *Migrate* runs a verify-and-curate pass against current code (grep each claim, ask about disagreements) before writing. See `references/brownfield.md`.

## Step 2 — tell agents how to use KEEP

`init.sh` appends the canonical KEEP snippet to `AGENTS.md` / `CLAUDE.md` / `.cursorrules` (whichever exists; creates `AGENTS.md` otherwise).

**Single source of truth: the `## AGENTS.md snippet — install in the repo` section of `SKILL.md`.** Don't duplicate the snippet here — duplication causes drift.

The snippet is intentionally hard:

- Frames `/keep-ask` as **mandatory** consultation before answering questions about behavior, design, history, or uncertainty about conventions. *"Before answering ANY of these, run `/keep-ask <topic>` first"* defeats the dominant failure mode (under-triggering on the read path).
- Says the agent must **explicitly say so** when `/keep-ask` returns no indexed knowledge, not fall back to generic knowledge as repo truth.
- Marks code changes touching behavior/architecture/operations as **incomplete** until `/keep-compile` runs.
- Wires `/keep-check-drift` into the merge path (`exit-code 1 blocks merge`) and `/keep-govern` to weekly hygiene.
- Carves out `/keep-idea` for half-formed thoughts.

Audit for a softened snippet — "mandatory" removed, "ANY" weakened to "some" — and restore the canonical version. The read path comes back to life.

## Step 3 — optional starting content

If the team has obvious decisions worth capturing (*"why Ray Serve?"*, *"why Auth0?"*), hand-write one or two ADRs on day one. Seeds `/keep-ask` with substance and sets the format tone.

Cap at three or four. Seed, don't backfill.

## Step 4 — verify the loop

On the next real change:

```
/keep-ask <topic>           # synthesize prior context, OR ask for list-only paths
[make the change]
/keep-compile               # classify + write + regen INDEX
                            # add --dry to preview
```

Sensible classification + the right questions → KEEP is set up correctly.

## Step 5 — wire drift into CI / pre-commit (recommended, not auto-installed)

`check_drift.py` exits 1 on any drifted anchor — real enforcement when wired into the merge path. KEEP does NOT auto-install hooks/workflows (invasive; depends on team conventions). Copy-paste templates:

### Pre-commit hook

Save as `.git/hooks/pre-commit`:

```bash
#!/usr/bin/env bash
set -e
SKILL_PATH="${SKILL_PATH:-$HOME/.claude/skills/keep}"
if [ ! -f "$SKILL_PATH/scripts/check_drift.py" ]; then
    echo "warning: KEEP skill not found — skipping drift check"
    exit 0
fi
python3 "$SKILL_PATH/scripts/check_drift.py" --knowledge knowledge --changed
```

`chmod +x .git/hooks/pre-commit`. Share via repo docs/setup script — git doesn't track hooks.

### GitHub Action

Save as `.github/workflows/keep-check-drift.yml`:

```yaml
name: KEEP — check drift
on:
  pull_request:
    paths:
      - 'knowledge/**'
      - '**.go'
      - '**.py'
      - '**.ts'
      - '**.tsx'

jobs:
  check-drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: git clone --depth 1 https://github.com/CuriousDolphin/KEEP /tmp/keep
      - run: python3 /tmp/keep/skills/KEEP/scripts/check_drift.py --knowledge knowledge
```

Adjust the checkout step to whatever distribution mechanism the team uses.

## Common adoption mistakes

- **Backfilling existing code.** Don't. Document as code changes; cordon existing docs and migrate piecemeal.
- **Bulk-migrating a docs folder.** Agent refuses and asks for file-by-file. The friction is the feature.
- **Treating KEEP as a task tracker.** KEEP stores durable knowledge only. Active work belongs in your tracker.
- **Running `/keep-govern` every cycle.** Weekly at most.
- **Multiple `/knowledge` directories in a monorepo.** One zone at the root. Splitting creates duplication.
- **Hand-editing `INDEX.md`.** Regenerated by `build_index.py`. If the layout is wrong, fix the script.

## Repository layout reminder

```
/knowledge
├── docs/
│   ├── specs/             (per-domain subdirs; runbook/architecture are tags)
│   └── decisions/         (flat — sequential ADRs)
├── ideas/                 (inbox for half-formed proposals)
└── INDEX.md               (auto-generated)
```

That's the whole surface area.
