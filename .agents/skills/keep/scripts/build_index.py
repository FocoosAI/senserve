#!/usr/bin/env python3
"""
build_index.py — regenerate /knowledge/INDEX.md from YAML frontmatter.

Why this script exists
----------------------
INDEX.md is a derived artifact: the source of truth is the YAML frontmatter
at the top of each knowledge file. Hand-maintaining the index produces messy
results (endpoint features under "Entities", runbook content under "Specs",
etc.). This script walks /knowledge/docs/ and /knowledge/ideas/, parses the
frontmatter, and emits a deterministic INDEX.md grouped by type (specs, ADRs,
runbooks-as-tag, architecture-as-tag) plus an auto-generated Backlinks section
derived from each file's `related:` block.

Run:
    python -m scripts.build_index <path-to-knowledge-root>
    # or, by absolute/relative path to wherever the skill is installed:
    python <skill-path>/scripts/build_index.py knowledge/

Exits 0 on success, 1 if any file has invalid or missing required frontmatter.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import pathlib
import re
import sys
from collections import defaultdict
from typing import Any, Iterable

# We try to use PyYAML if available; otherwise fall back to a minimal parser
# that handles the subset of YAML we use (scalars, lists, simple dicts). The
# fallback exists so the script runs in environments where pip install isn't
# trivial — KEEP shouldn't force a dependency to update an index.
try:
    import yaml  # type: ignore
    _HAS_PYYAML = True
except ImportError:
    _HAS_PYYAML = False


REQUIRED_FIELDS = ("title", "description", "status", "type", "domain", "tags")
VALID_STATUSES = {"draft", "accepted", "deprecated", "superseded"}
VALID_TYPES = {"spec", "adr", "idea"}  # runbook/architecture are tags on specs


@dataclasses.dataclass
class KnowledgeFile:
    """One parsed knowledge file with its frontmatter and path."""
    path: pathlib.Path
    relpath: str          # relative to /knowledge root
    frontmatter: dict[str, Any]
    issues: list[str] = dataclasses.field(default_factory=list)

    @property
    def id(self) -> str:
        # Prefer explicit id, else derive from filename
        return self.frontmatter.get("id") or self.path.stem

    @property
    def type(self) -> str:
        return self.frontmatter.get("type", "")

    @property
    def domain(self) -> str:
        return self.frontmatter.get("domain", "")

    @property
    def status(self) -> str:
        return self.frontmatter.get("status", "")

    @property
    def tags(self) -> list[str]:
        v = self.frontmatter.get("tags", [])
        return v if isinstance(v, list) else [str(v)]


def parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Return (frontmatter_dict, body) or (None, text) if no frontmatter."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return None, text
    raw = text[4:end]
    body = text[end + 5 :]
    if _HAS_PYYAML:
        try:
            data = yaml.safe_load(raw) or {}
            return data, body
        except yaml.YAMLError:
            return None, text
    # Minimal fallback parser — handles scalars, simple lists, and the
    # `related:` block we use. Not a general YAML parser, but covers our schema.
    return _parse_yaml_subset(raw), body


def _parse_yaml_subset(raw: str) -> dict[str, Any]:
    """Tiny YAML subset parser for environments without PyYAML."""
    out: dict[str, Any] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line or line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2)
        if val == "":
            # block: inline list of `- item` until next top-level key
            items: list[Any] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.startswith((" ", "\t", "-")) and not nxt.startswith("  -"):
                    if not nxt.lstrip().startswith("-"):
                        break
                stripped = nxt.lstrip()
                if stripped.startswith("- "):
                    items.append(stripped[2:].strip().strip('"'))
                    i += 1
                elif stripped.startswith("-"):
                    items.append(stripped[1:].strip().strip('"'))
                    i += 1
                else:
                    break
            out[key] = items
            continue
        # Inline value — could be scalar or [a, b, c]
        v = val.strip().strip('"')
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            out[key] = [s.strip().strip('"') for s in inner.split(",") if s.strip()]
        else:
            out[key] = v
        i += 1
    return out


def validate(kf: KnowledgeFile) -> None:
    """Populate kf.issues with any problems found in the frontmatter."""
    fm = kf.frontmatter
    for required in REQUIRED_FIELDS:
        if required not in fm:
            kf.issues.append(f"missing required field: {required}")
    if fm.get("status") and fm["status"] not in VALID_STATUSES:
        kf.issues.append(
            f"invalid status '{fm['status']}'; allowed: {sorted(VALID_STATUSES)}"
        )
    if fm.get("type") and fm["type"] not in VALID_TYPES:
        kf.issues.append(
            f"invalid type '{fm['type']}'; allowed: {sorted(VALID_TYPES)}"
        )
    if fm.get("tags") is not None and not isinstance(fm["tags"], list):
        kf.issues.append("tags must be a list")


def walk_knowledge(root: pathlib.Path) -> Iterable[KnowledgeFile]:
    """Yield parsed KnowledgeFile entries from /knowledge/docs/ and /knowledge/ideas/."""
    for sub in ("docs", "ideas"):
        base = root / sub
        if not base.exists():
            continue
        for md_path in sorted(base.rglob("*.md")):
            text = md_path.read_text(encoding="utf-8")
            fm, _body = parse_frontmatter(text)
            if fm is None:
                # File without frontmatter — flag it but still surface so the user knows
                kf = KnowledgeFile(
                    path=md_path,
                    relpath=str(md_path.relative_to(root)),
                    frontmatter={},
                )
                kf.issues.append("no YAML frontmatter found")
                yield kf
                continue
            kf = KnowledgeFile(
                path=md_path,
                relpath=str(md_path.relative_to(root)),
                frontmatter=fm,
            )
            validate(kf)
            yield kf


def render_table(rows: list[tuple], headers: tuple[str, ...]) -> str:
    """Render a markdown table from a list of row tuples.

    Cells are coerced to str before escaping. YAML scalars sometimes come back
    as datetime.date / int — coercing here keeps callers from having to remember.
    """
    if not rows:
        return "_(none)_\n"
    out = ["| " + " | ".join(headers) + " |"]
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        escaped = [str(c).replace("|", "\\|") for c in row]
        out.append("| " + " | ".join(escaped) + " |")
    return "\n".join(out) + "\n"


def truncate(text: str, n: int = 120) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


# Knowledge-link prefixes we follow into the reverse graph. `code:` and `test:`
# point at the codebase, not at other knowledge files, so they don't appear
# here — they're handled by check_drift, not by the index.
_BACKLINK_PREFIXES = ("adr:", "spec:", "idea:", "runbook:")


def _extract_backlinks(files: list[KnowledgeFile]) -> dict[str, list[str]]:
    """Build the reverse-reference graph from `related:` blocks.

    For every file F whose `related:` mentions another knowledge id T, record
    F as a referrer of T. The result maps target_id -> [source_id, ...].

    This powers Karpathy-style automatic backlinks: each file's index entry can
    show "who points at me?" without anyone having to maintain the reverse side
    by hand.
    """
    backlinks: dict[str, list[str]] = defaultdict(list)
    for kf in files:
        if kf.issues:
            continue
        related = kf.frontmatter.get("related", []) or []
        if not isinstance(related, list):
            continue
        for ref in related:
            if not isinstance(ref, str):
                continue
            for prefix in _BACKLINK_PREFIXES:
                if ref.startswith(prefix):
                    target = ref[len(prefix):].strip()
                    # Strip trailing notes like "ADR-0007  # supersedes"
                    target = target.split("#", 1)[0].strip()
                    if target and kf.id not in backlinks[target]:
                        backlinks[target].append(kf.id)
                    break
    # Sort each referrer list for stable output
    for k in backlinks:
        backlinks[k].sort()
    return dict(backlinks)


def build_index(root: pathlib.Path) -> tuple[str, list[KnowledgeFile]]:
    """Build the INDEX.md content and return it with the list of files."""
    files = list(walk_knowledge(root))
    backlinks = _extract_backlinks(files)

    by_type: dict[str, list[KnowledgeFile]] = defaultdict(list)
    by_tag_runbook: list[KnowledgeFile] = []
    by_tag_architecture: list[KnowledgeFile] = []
    ideas: list[KnowledgeFile] = []
    superseded: list[KnowledgeFile] = []
    issues: list[KnowledgeFile] = []

    for kf in files:
        if kf.issues:
            issues.append(kf)
            continue
        if kf.type == "idea":
            ideas.append(kf)
            continue
        if kf.status == "superseded":
            superseded.append(kf)
            continue
        by_type[kf.type].append(kf)
        # `runbook` and `architecture` are reserved tags that modify the *role* of a
        # spec (behavioral → operational, behavioral → topological). They are NOT
        # generic descriptors. ADRs and ideas with these tags do not belong in these
        # INDEX sections — their nature is already captured by `type:`. Filtering by
        # type+tag here keeps the section title's promise: "specs with tag ...".
        if kf.type == "spec" and "runbook" in kf.tags:
            by_tag_runbook.append(kf)
        if kf.type == "spec" and "architecture" in kf.tags:
            by_tag_architecture.append(kf)

    # Sort each group: by domain, then by id
    def _key(k: KnowledgeFile) -> tuple[str, str]:
        return (k.domain, k.id)

    today = dt.date.today().isoformat()
    out: list[str] = [
        "# INDEX",
        "",
        f"_Auto-generated by `scripts/build_index.py` on {today}._",
        f"_Do not edit by hand — re-run the script after any /keep-compile._",
        "",
        f"**Files indexed:** {len(files)} total, "
        f"{sum(1 for f in files if not f.issues)} valid, "
        f"{len(issues)} with frontmatter issues.",
        "",
    ]

    out.append("## Specs")
    out.append("")
    rows = []
    for kf in sorted(by_type.get("spec", []), key=_key):
        rows.append((
            kf.id,
            truncate(kf.frontmatter.get("title", ""), 50),
            truncate(kf.frontmatter.get("description", ""), 100),
            kf.domain,
            kf.status,
            ", ".join(kf.tags),
            kf.relpath,
        ))
    out.append(render_table(rows, ("ID", "Title", "Description", "Domain", "Status", "Tags", "Path")))
    out.append("")

    out.append("## ADRs")
    out.append("")
    rows = []
    for kf in sorted(by_type.get("adr", []), key=_key):
        rows.append((
            kf.id,
            truncate(kf.frontmatter.get("title", ""), 50),
            truncate(kf.frontmatter.get("description", ""), 100),
            kf.domain,
            kf.status,
            ", ".join(kf.tags),
            kf.relpath,
        ))
    out.append(render_table(rows, ("ID", "Title", "Description", "Domain", "Status", "Tags", "Path")))
    out.append("")

    if by_tag_runbook:
        out.append("## Runbooks (specs with tag `runbook`)")
        out.append("")
        rows = []
        for kf in sorted(by_tag_runbook, key=_key):
            rows.append((
                kf.id,
                truncate(kf.frontmatter.get("title", ""), 50),
                truncate(kf.frontmatter.get("description", ""), 100),
                kf.domain,
                kf.relpath,
            ))
        out.append(render_table(rows, ("ID", "Title", "Description", "Domain", "Path")))
        out.append("")

    if by_tag_architecture:
        out.append("## Architecture (specs with tag `architecture`)")
        out.append("")
        rows = []
        for kf in sorted(by_tag_architecture, key=_key):
            rows.append((
                kf.id,
                truncate(kf.frontmatter.get("title", ""), 50),
                truncate(kf.frontmatter.get("description", ""), 100),
                kf.domain,
                kf.relpath,
            ))
        out.append(render_table(rows, ("ID", "Title", "Description", "Domain", "Path")))
        out.append("")

    if ideas:
        out.append("## Ideas (inbox — not yet promoted)")
        out.append("")
        rows = []
        for kf in sorted(ideas, key=_key):
            rows.append((
                kf.id,
                truncate(kf.frontmatter.get("title", ""), 50),
                truncate(kf.frontmatter.get("description", ""), 80),
                kf.frontmatter.get("created", ""),
                ", ".join(kf.tags),
                kf.relpath,
            ))
        out.append(render_table(rows, ("ID", "Title", "Description", "Created", "Tags", "Path")))
        out.append("")

    if superseded:
        out.append("## Superseded")
        out.append("")
        out.append(
            "_Historical decisions kept for traceability. "
            "Do not act on these — see the superseding entry above._"
        )
        out.append("")
        rows = []
        for kf in sorted(superseded, key=_key):
            rows.append((
                kf.id,
                truncate(kf.frontmatter.get("title", ""), 50),
                kf.domain,
                kf.relpath,
            ))
        out.append(render_table(rows, ("ID", "Title", "Domain", "Path")))
        out.append("")

    if backlinks:
        out.append("## Backlinks")
        out.append("")
        out.append(
            "_Reverse view of `related:` references. "
            "Useful when answering *\"who depends on this decision?\"* without grepping. "
            "Auto-derived; do not hand-edit._"
        )
        out.append("")
        rows = []
        # Sort by target id for stable output
        for target_id in sorted(backlinks):
            referrers = backlinks[target_id]
            rows.append((target_id, str(len(referrers)), ", ".join(referrers)))
        out.append(render_table(rows, ("Referenced ID", "# Referrers", "Referrers")))
        out.append("")

    if issues:
        out.append("## ⚠ Files with frontmatter issues")
        out.append("")
        out.append(
            "_These files were skipped from the main listing. "
            "Fix their frontmatter and re-run `build_index.py`._"
        )
        out.append("")
        for kf in issues:
            out.append(f"- `{kf.relpath}` — {'; '.join(kf.issues)}")
        out.append("")

    return "\n".join(out), files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate /knowledge/INDEX.md from YAML frontmatter."
    )
    parser.add_argument(
        "knowledge_root",
        type=pathlib.Path,
        help="Path to the /knowledge directory (the one that contains docs/ and INDEX.md).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the new INDEX.md to stdout instead of writing it.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any file has frontmatter issues.",
    )
    args = parser.parse_args(argv)

    root = args.knowledge_root.resolve()
    if not (root / "docs").exists() and not (root / "ideas").exists():
        print(
            f"error: {root} does not look like a /knowledge directory "
            f"(no docs/ or ideas/ subfolder)",
            file=sys.stderr,
        )
        return 2

    content, files = build_index(root)
    issues_count = sum(1 for f in files if f.issues)

    if args.dry_run:
        print(content)
    else:
        target = root / "INDEX.md"
        target.write_text(content, encoding="utf-8")
        print(f"wrote {target} ({len(files)} files, {issues_count} with issues)")

    if args.strict and issues_count:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
