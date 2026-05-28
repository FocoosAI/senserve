#!/usr/bin/env python3
"""KEEP status — quick dashboard of /knowledge state.

Used by the /keep slash command as a one-line health check.

Exit codes:
  0 — /knowledge exists, status printed
  2 — /knowledge does not exist (caller should offer to run init.sh)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _keep import Spec, find_specs

try:
    from coverage import summary as code_coverage_summary
except ImportError:
    code_coverage_summary = None  # coverage.py is optional — degrade gracefully


def main():
    parser = argparse.ArgumentParser(description="KEEP status — repo-level dashboard")
    parser.add_argument("--knowledge", default="knowledge", help="Path to /knowledge/ root")
    parser.add_argument("--repo", default=None, help="Repo root (default: parent of --knowledge)")
    args = parser.parse_args()

    knowledge = Path(args.knowledge).resolve()
    if not knowledge.exists() or not knowledge.is_dir():
        print(f"KEEP not initialized: {knowledge} does not exist.")
        print("Run: bash <skill-path>/scripts/init.sh  (or via /keep, which will offer to run it)")
        sys.exit(2)

    specs: list[Spec] = []
    adrs: list[Spec] = []
    ideas: list[Spec] = []
    total_anchors = 0
    specs_with_anchors = 0
    drafts_aging: list[Spec] = []     # ideas in draft (count only; date check is /keep-govern's job)

    for path in find_specs(knowledge):
        spec = Spec.load(path)
        ftype = str(spec.frontmatter.get("type", ""))
        if ftype == "spec":
            specs.append(spec)
        elif ftype == "adr":
            adrs.append(spec)
        elif ftype == "idea":
            ideas.append(spec)
            if str(spec.frontmatter.get("status", "")) == "draft":
                drafts_aging.append(spec)
        if spec.anchors:
            specs_with_anchors += 1
            total_anchors += len(spec.anchors)

    total_durable = len(specs) + len(adrs)
    coverage_pct = (specs_with_anchors / total_durable * 100) if total_durable else 0.0

    # Code-level coverage (separate from spec-level coverage above)
    code_cov_line = ""
    code_pct: float = 0.0
    code_total: int = 0
    if code_coverage_summary is not None:
        repo_root = Path(args.repo).resolve() if args.repo else knowledge.parent
        try:
            anchored, code_total, code_pct = code_coverage_summary(knowledge, repo_root)
            code_cov_line = (
                f"  code:      {anchored}/{code_total} top-level symbols anchored "
                f"({code_pct:.0f}% coverage of supported source files)"
            )
        except Exception:
            # Best-effort — coverage should never block the dashboard
            pass

    print(f"KEEP status — {knowledge}")
    print(f"  specs:     {len(specs)}")
    print(f"  ADRs:      {len(adrs)}")
    print(f"  ideas:     {len(ideas)} ({len(drafts_aging)} in draft)")
    print(
        f"  anchors:   {total_anchors} across {specs_with_anchors}/{total_durable}"
        f" durable files ({coverage_pct:.0f}% of specs/ADRs are anchored)"
    )
    if code_cov_line:
        print(code_cov_line)

    # Surface hints based on state
    hints: list[str] = []
    if total_durable == 0:
        hints.append(
            "no knowledge files yet — capture your first idea with `/keep-idea \"...\"`"
            " or run `/keep-compile` after a non-trivial change."
        )
    if total_durable > 0 and coverage_pct < 50:
        hints.append(
            "anchor coverage is low — anchors make `/keep-check-drift` deterministic."
            " Add `anchors:` blocks to spec frontmatter (schema in references/file_formats.md)."
        )
    if code_total > 0 and code_pct < 25:
        hints.append(
            f"code-level anchor coverage is {code_pct:.0f}% — most top-level symbols "
            "are not enforced by drift detection. Run `scripts/coverage.py` for a "
            "per-file breakdown of unanchored symbols."
        )
    index_md = knowledge / "INDEX.md"
    if index_md.exists():
        body = index_md.read_text()
        if "auto-generated" not in body and total_durable > 0:
            hints.append(
                "INDEX.md is not the auto-generated form — run `/keep-compile` to regenerate."
            )

    if hints:
        print("\nHints:")
        for h in hints:
            print(f"  - {h}")

    sys.exit(0)


if __name__ == "__main__":
    main()
