
## KEEP — Knowledge layer for this repository

`/knowledge/` is the authoritative source of truth for:
- WHAT this system does (specs)
- WHY it is built this way (ADRs)
- HOW it operates under failure (specs with tag `runbook`)
- WHERE components live and connect (specs with tag `architecture`)
- WHAT we've been thinking about but not yet committed to (ideas)

### Mandatory consultation before responding

Before answering ANY of these, run `/keep-ask <topic>` first:
- Questions about system behavior, design, or history
- Questions naming a feature, service, ADR, endpoint, or domain
- Requests to implement, modify, refactor, or remove existing behavior
- Any uncertainty about whether a decision or convention exists

If `/keep-ask` returns "no indexed knowledge", say so explicitly in your
answer — do NOT fall back to generic knowledge as if it were repo truth.

### After non-trivial code changes

Run `/keep-compile` before declaring the work done. A change that touches
behavior, architecture, or operations without updating `/knowledge` is
incomplete.

### Before merging

Run `/keep-check-drift` on the diff. Drift exit-code 1 blocks merge.

### Periodic

`/keep-govern` weekly for hygiene.

### Capture, don't drop

Half-formed ideas → `/keep-idea "..."`. Don't lose them in chat.
