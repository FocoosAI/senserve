---
description: Capture a half-formed idea durably in /knowledge/ideas/ — not yet a spec or ADR
argument-hint: <idea description, free-form>
---

Idea: `$ARGUMENTS`.

For when the user describes a proposal but doesn't want to implement it now — and doesn't want to lose it in chat history. Ideas live in `/knowledge/ideas/` with `type: idea`; they're the bridge between exploratory thinking and durable knowledge and can later be promoted via `/keep-compile`.

## Contract

1. **Search for prior art first**. Quick scan of `/knowledge/` for prior ideas/runbooks/ADRs that overlap. If something exists, surface it — the user often half-remembers prior thinking and is asking precisely because they don't trust their memory.
2. **Create `/knowledge/ideas/<YYYY-MM-DD>-<slug>.md`** with frontmatter:
   - `id: IDEA-<YYYY-MM-DD>-<slug>`
   - `type: idea`, `status: draft`
   - `domain: <inferred>`, `created: <today>`, `tags: [...]`
   - `related:` — link to prior art if any
3. **Body**: capture *what the user actually said*, not your interpretation. Add a "Context" section if the trigger is clear. Add "Open questions" listing what would need to be resolved before promoting.
4. **Don't create a spec or ADR yet.** That's premature — ideas are deliberately separate from durable artifacts because committing to a spec/ADR implies a decision.
5. **Confirm briefly**: path of the file, prior art found, suggested next step (e.g. *"could become an ADR once we settle on dual-secret vs JWKS"*).

## Hard rules

- Ideas live in `/knowledge/ideas/` only. Execution state (active work) belongs in your ticket tracker.
- Never auto-promote into a spec/ADR. Promotion happens in `/keep-compile` with explicit approval.
- Preserve the user's wording. Paraphrasing destroys signal — they'll read this later and need to recognize their own thought.
- Use `related:` to keep the graph connected — an idea can reference a runbook, an ADR, another idea.

## Lifecycle

- `status: draft` — initial capture.
- Promoted via `/keep-compile` — original idea stays in `ideas/` with `status: deprecated` + `related:` pointing to the resulting spec/ADR. Don't delete; the original thought is part of the lineage.
- Dropped without promotion — `status: deprecated` with a one-line note.
- `/keep-govern` flags ideas older than 30 days still in `draft`.
