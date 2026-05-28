#!/usr/bin/env python3
"""KEEP /keep coverage — code-level anchor coverage.

Reverse of check_drift: given /knowledge/ and a code root, report what
percentage of top-level symbols (constants, named functions, named tests)
in the codebase are referenced by at least one anchor in some spec.

Exit codes:
  0 — report produced
  2 — /knowledge does not exist

The script is deterministic, stdlib-only, no LLM. The point is to surface
the silent KPI of a KEEP-enabled repo: anchored facts vs. unanchored facts.
A repo with thorough drift detection in /keep-check-drift still has zero
value if no anchors exist.

Coverage is a guidance metric, not a gate. There is no failure exit code
for "low coverage" — that would push users into anchor coverage theater
(anchoring trivial values to bump the number). Instead, the report
identifies *which* files and *which* domains have gaps, so the next
spec authoring session knows where to focus.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from _keep import (
    Spec, LangAdapter,
    GO, PYTHON, TS, ALL_LANGS,
    detect_language, find_specs,
)


# ---------------------------------------------------------------------------
# Symbol extraction — per language, top-level only
# ---------------------------------------------------------------------------


@dataclass
class CodeSymbol:
    name: str
    kind: str   # "const" | "function" | "test"
    file: str
    line: int


def _extract_go(text: str) -> list[tuple[str, str, int]]:
    """Top-level Go symbols. Returns (name, kind, line)."""
    out: list[tuple[str, str, int]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        # const Name = ... | const ( ... ) is multi-line — handled below per-line
        m = re.match(r"^const\s+(\w+)\b", line)
        if m:
            out.append((m.group(1), "const", i))
            continue
        m = re.match(r"^var\s+(\w+)\b", line)
        if m:
            out.append((m.group(1), "const", i))
            continue
        m = re.match(r"^func\s+(\w+)\s*\(", line)
        if m:
            name = m.group(1)
            kind = "test" if name.startswith("Test") else "function"
            out.append((name, kind, i))
            continue
    return out


def _extract_python(text: str) -> list[tuple[str, str, int]]:
    """Top-level Python symbols (column 0). Skips indented (=class methods).

    Heuristic: only zero-indented declarations are "top-level". Decorators
    above defs are ignored — the def line is what we capture.
    """
    out: list[tuple[str, str, int]] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        if line and line[0].isspace():
            continue
        m = re.match(r"^def\s+(\w+)\s*\(", line)
        if m:
            name = m.group(1)
            kind = "test" if name.startswith("test_") else "function"
            out.append((name, kind, i))
            continue
        # Top-level assignment: NAME = ...  or NAME: type = ...
        m = re.match(r"^([A-Z][A-Z0-9_]*)\s*(?::[^=]+)?=\s*", line)
        if m:
            out.append((m.group(1), "const", i))
    return out


def _extract_ts(text: str) -> list[tuple[str, str, int]]:
    """TypeScript: exported top-level const/let/function + jest test/it calls."""
    out: list[tuple[str, str, int]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        # export const X = ...   |   export let X = ...
        m = re.match(r"^(?:export\s+)?(?:const|let|var)\s+([A-Z][A-Za-z0-9_]*)\b", line)
        if m:
            out.append((m.group(1), "const", i))
            continue
        # export function X(...) | function X(...) | export const X = (
        m = re.match(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][\w$]*)\s*\(", line)
        if m:
            out.append((m.group(1), "function", i))
            continue
        # test('name', ...) | it('name', ...) at the start of a line
        m = re.match(r"^\s*(?:test|it)\s*\(\s*['\"`]([^'\"`]+)['\"`]", line)
        if m:
            out.append((m.group(1), "test", i))
    return out


_EXTRACTORS = {
    "go": _extract_go,
    "python": _extract_python,
    "typescript": _extract_ts,
}


def extract_symbols(file_path: Path) -> list[CodeSymbol]:
    """Extract top-level symbols from a source file.

    Returns empty list for unsupported languages — the file is silently
    skipped from coverage. (Anchors with kind=manual on unsupported files
    are reported separately by check_drift; coverage doesn't need to track
    them since there's nothing to anchor *against*.)
    """
    lang = detect_language(str(file_path))
    if lang is None:
        return []
    try:
        text = file_path.read_text(errors="replace")
    except OSError:
        return []
    extractor = _EXTRACTORS.get(lang.name)
    if extractor is None:
        return []
    rel = str(file_path)
    return [CodeSymbol(name=n, kind=k, file=rel, line=l) for (n, k, l) in extractor(text)]


# ---------------------------------------------------------------------------
# Anchor extraction — what's claimed by /knowledge?
# ---------------------------------------------------------------------------


@dataclass
class AnchoredSymbol:
    file: str
    symbol: str
    kind: str       # const | function | test | manual
    spec_id: str
    anchor_id: str


def collect_anchored_symbols(knowledge_root: Path) -> list[AnchoredSymbol]:
    """Read every spec's anchors block and flatten into (file, symbol, kind)."""
    out: list[AnchoredSymbol] = []
    for path in find_specs(knowledge_root):
        spec = Spec.load(path)
        for a in spec.anchors:
            if a.kind == "manual":
                # Manual anchors don't contribute to code coverage by design
                continue
            if not a.file or not a.symbol:
                continue
            out.append(AnchoredSymbol(
                file=a.file,
                symbol=a.symbol,
                kind=a.kind,
                spec_id=spec.spec_id,
                anchor_id=a.id,
            ))
    return out


# ---------------------------------------------------------------------------
# Domain mapping — file -> domain (via spec.related code:patterns)
# ---------------------------------------------------------------------------


def build_domain_map(knowledge_root: Path) -> dict[str, str]:
    """Map relative source path -> domain (via spec frontmatter `related: code:...`).

    A file may be claimed by multiple specs in multiple domains. We use the
    first-wins rule and surface multi-claim in the report later if useful.
    Files not claimed by any spec land under 'unassigned'.
    """
    mapping: dict[str, str] = {}
    for path in find_specs(knowledge_root):
        spec = Spec.load(path)
        domain = str(spec.frontmatter.get("domain", "") or "unassigned")
        related = spec.frontmatter.get("related", []) or []
        if not isinstance(related, list):
            continue
        for ref in related:
            if not isinstance(ref, str) or not ref.startswith("code:"):
                continue
            target = ref[len("code:"):].strip()
            # Resolve patterns conservatively — for an unambiguous single file we just
            # record it. For globs (e.g. internal/auth/*_test.go) we don't expand here;
            # the per-file analysis below will associate by directory prefix anyway.
            mapping.setdefault(target, domain)
        # Also anchor files contribute their domain (an anchor IS code:file)
        for a in spec.anchors:
            if a.file:
                mapping.setdefault(a.file, domain)
    return mapping


def file_to_domain(file_path: str, mapping: dict[str, str]) -> str:
    """Lookup with directory-prefix fallback."""
    if file_path in mapping:
        return mapping[file_path]
    # Prefix match — if some spec claims a directory via a wildcard pattern
    for known, domain in mapping.items():
        if "*" in known:
            # Build a simple prefix from before the first wildcard
            prefix = known.split("*", 1)[0]
            if file_path.startswith(prefix):
                return domain
    return "unassigned"


# ---------------------------------------------------------------------------
# Walk source tree
# ---------------------------------------------------------------------------


_IGNORE_DIRS = {
    "node_modules", ".git", "dist", "build", "target", "vendor",
    ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    "knowledge",   # don't count knowledge files themselves
}


def walk_code(root: Path) -> list[Path]:
    """Yield source files KEEP can extract symbols from."""
    out: list[Path] = []
    valid_suffixes = set()
    for lang in ALL_LANGS:
        valid_suffixes.update(lang.extensions)
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in valid_suffixes:
            continue
        # Skip if any path component is in _IGNORE_DIRS
        if any(part in _IGNORE_DIRS for part in p.relative_to(root).parts):
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Coverage computation
# ---------------------------------------------------------------------------


@dataclass
class FileCoverage:
    relpath: str
    domain: str
    total_symbols: int = 0
    anchored_symbols: int = 0
    anchored: list[tuple[str, str]] = field(default_factory=list)    # (symbol, kind)
    unanchored: list[tuple[str, str]] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return (self.anchored_symbols / self.total_symbols * 100) if self.total_symbols else 0.0


def compute(knowledge_root: Path, repo_root: Path) -> list[FileCoverage]:
    """Walk code, walk knowledge, compute per-file coverage."""
    anchored = collect_anchored_symbols(knowledge_root)
    domain_map = build_domain_map(knowledge_root)

    # Build (file, symbol) -> anchored? lookup
    anchored_set: set[tuple[str, str]] = {(a.file, a.symbol) for a in anchored}

    results: list[FileCoverage] = []
    for code_path in walk_code(repo_root):
        rel = str(code_path.relative_to(repo_root))
        domain = file_to_domain(rel, domain_map)
        fc = FileCoverage(relpath=rel, domain=domain)
        symbols = extract_symbols(code_path)
        for sym in symbols:
            fc.total_symbols += 1
            if (rel, sym.name) in anchored_set:
                fc.anchored_symbols += 1
                fc.anchored.append((sym.name, sym.kind))
            else:
                fc.unanchored.append((sym.name, sym.kind))
        if fc.total_symbols > 0:
            results.append(fc)
    return results


def aggregate_by_domain(files: list[FileCoverage]) -> dict[str, dict]:
    """Per-domain aggregate: total symbols, anchored symbols, % coverage."""
    agg: dict[str, dict] = defaultdict(lambda: {"total": 0, "anchored": 0, "files": 0})
    for fc in files:
        agg[fc.domain]["total"] += fc.total_symbols
        agg[fc.domain]["anchored"] += fc.anchored_symbols
        agg[fc.domain]["files"] += 1
    for d in agg.values():
        d["pct"] = (d["anchored"] / d["total"] * 100) if d["total"] else 0.0
    return dict(agg)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report_text(files: list[FileCoverage]) -> str:
    """Human-readable report. Sorted by lowest coverage first."""
    out: list[str] = []
    agg = aggregate_by_domain(files)
    total_symbols = sum(f.total_symbols for f in files)
    total_anchored = sum(f.anchored_symbols for f in files)
    aggregate_pct = (total_anchored / total_symbols * 100) if total_symbols else 0.0

    out.append("=== KEEP anchor coverage ===")
    out.append("")
    out.append(f"  Overall: {total_anchored}/{total_symbols} symbols anchored ({aggregate_pct:.0f}%)")
    out.append(f"  Files scanned: {len(files)} (Go / Python / TypeScript)")
    out.append("")
    out.append("By domain (lowest first):")
    for domain in sorted(agg, key=lambda d: agg[d]["pct"]):
        d = agg[domain]
        out.append(
            f"  {domain:20s} {d['anchored']:>3d}/{d['total']:<3d} "
            f"({d['pct']:5.1f}%)  across {d['files']} file(s)"
        )
    out.append("")

    # Per-file detail for the worst offenders (coverage < 50% AND >= 3 symbols)
    worst = sorted(
        [f for f in files if f.total_symbols >= 3 and f.pct < 50],
        key=lambda f: (f.pct, -f.total_symbols),
    )
    if worst:
        out.append("Files with low coverage (<50%, at least 3 symbols):")
        for f in worst[:20]:
            out.append(f"  {f.relpath}  —  {f.anchored_symbols}/{f.total_symbols} ({f.pct:.0f}%)  domain={f.domain}")
            if f.unanchored:
                names = ", ".join(s for s, _ in f.unanchored[:5])
                more = "" if len(f.unanchored) <= 5 else f"  (+{len(f.unanchored) - 5} more)"
                out.append(f"      unanchored: {names}{more}")
        out.append("")

    out.append(
        "Coverage is guidance, not a gate. Low coverage means /keep-check-drift "
        "is enforcing fewer facts than it could — but anchoring trivial values "
        "to bump the number is anchor coverage theater. Anchor what *must* hold."
    )
    return "\n".join(out)


def report_json(files: list[FileCoverage]) -> str:
    """Machine-readable report."""
    agg = aggregate_by_domain(files)
    total_symbols = sum(f.total_symbols for f in files)
    total_anchored = sum(f.anchored_symbols for f in files)
    payload = {
        "overall": {
            "total_symbols": total_symbols,
            "anchored_symbols": total_anchored,
            "pct": (total_anchored / total_symbols * 100) if total_symbols else 0.0,
            "files_scanned": len(files),
        },
        "by_domain": agg,
        "files": [
            {
                "path": f.relpath,
                "domain": f.domain,
                "total": f.total_symbols,
                "anchored": f.anchored_symbols,
                "pct": f.pct,
                "unanchored_symbols": [{"name": n, "kind": k} for (n, k) in f.unanchored],
            }
            for f in files
        ],
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# Public API used by status.py
# ---------------------------------------------------------------------------


def summary(knowledge_root: Path, repo_root: Path) -> tuple[int, int, float]:
    """Tiny helper for /keep dashboard: (anchored, total, pct)."""
    files = compute(knowledge_root, repo_root)
    total_symbols = sum(f.total_symbols for f in files)
    total_anchored = sum(f.anchored_symbols for f in files)
    pct = (total_anchored / total_symbols * 100) if total_symbols else 0.0
    return total_anchored, total_symbols, pct


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="KEEP coverage — % of top-level code symbols referenced by an anchor"
    )
    parser.add_argument("--knowledge", default="knowledge", help="Path to /knowledge/ root")
    parser.add_argument("--repo", default=None, help="Repo root (default: parent of --knowledge)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human report")
    args = parser.parse_args()

    knowledge = Path(args.knowledge).resolve()
    if not knowledge.exists():
        print(f"error: {knowledge} does not exist", file=sys.stderr)
        sys.exit(2)
    repo = Path(args.repo).resolve() if args.repo else knowledge.parent

    files = compute(knowledge, repo)
    if args.json:
        print(report_json(files))
    else:
        print(report_text(files))
    sys.exit(0)


if __name__ == "__main__":
    main()
