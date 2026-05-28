#!/usr/bin/env python3
"""KEEP /keep-check-drift — deterministic drift checker.

For every anchor declared in spec frontmatter under /knowledge/, verify that
the target source file still satisfies the anchor's binding.

Exit codes:
  0 — no drift (or only manual/ok anchors)
  1 — at least one anchor in drift / missing target file
  2 — usage error / knowledge root missing

Anchor kinds supported:
  - const     : symbol = value, language-specific match
  - function  : signature equality
  - test      : function or test case exists AND is not skipped
  - manual    : reported only; never causes failure

Languages supported: Go, Python, TypeScript. Unsupported file extensions
produce a `missing` result (not silently skipped — surfaces an honest gap).

The script is stdlib-only and deterministic. No LLM is involved.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from _keep import (
    GO, PYTHON, TS, Anchor, LangAdapter, Spec,
    detect_language, find_specs,
)


# ---------------------------------------------------------------------------
# Result reporting
# ---------------------------------------------------------------------------

@dataclass
class DriftResult:
    spec_id: str
    anchor_id: str
    kind: str       # "ok" | "drift" | "missing" | "manual"
    detail: str

    @property
    def marker(self) -> str:
        return {"ok": "OK", "drift": "DRIFT", "missing": "MISSING", "manual": "MANUAL"}[self.kind]


# ---------------------------------------------------------------------------
# Per-kind / per-language matchers
# ---------------------------------------------------------------------------


def _normalize_value(s: str) -> str:
    """Normalize a literal value for equality. Strips ALL whitespace.

    Used for const values: `5*time.Minute` and `5 * time.Minute` both become
    `5*time.Minute`. Safe because const values don't have semantic whitespace.
    """
    return re.sub(r"\s+", "", s.strip())


def _normalize_signature(s: str) -> str:
    """Normalize a function signature: collapse internal whitespace to single space.

    Preserves semantic spaces (between identifier and type, etc.) so
    `(aud string)` and `(audstring)` don't match. Also strips Go-style trailing
    commas in multiline parameter lists so `(a, b,)` matches `(a, b)`.
    """
    s = re.sub(r"\s+", " ", s.strip())
    # Strip optional spaces around punctuation that don't change meaning
    s = re.sub(r"\s*([,()\[\]])\s*", r"\1", s)
    # Drop trailing commas before closing brackets — Go allows them in multiline params
    s = re.sub(r",([)\]])", r"\1", s)
    return s


def check_const(text: str, lang: LangAdapter, symbol: str, expected: str) -> tuple[bool, str]:
    """Find `symbol = value` declaration and compare to expected.

    All patterns are anchored at start-of-line (after optional indentation) so
    commented-out declarations (`// const X = 5`) are NOT matched. This is the
    cheap regex equivalent of "only consider top-level declarations".
    """
    if lang.name == "go":
        # const X = ...   or   const X type = ...   or   short-decl  X := ...
        patterns = [
            rf"^[ \t]*const\s+{re.escape(symbol)}(?:\s+[\w\.\*\[\]]+)?\s*=\s*([^\n;]+?)(?=\s*(?://|$|\n))",
            rf"^[ \t]*var\s+{re.escape(symbol)}(?:\s+[\w\.\*\[\]]+)?\s*=\s*([^\n;]+?)(?=\s*(?://|$|\n))",
            rf"^[ \t]*{re.escape(symbol)}\s*:=\s*([^\n;]+?)(?=\s*(?://|$|\n))",
        ]
    elif lang.name == "python":
        # X = ...   or   X: type = ...
        # Anchored at start-of-line; comments (#) excluded.
        patterns = [
            rf"^[ \t]*{re.escape(symbol)}(?:\s*:\s*[^=\n]+?)?\s*=\s*(.+?)(?:\s*(?:#|$))",
        ]
    elif lang.name == "typescript":
        # const X = ...   or   export const X: type = ...;   or   let X = ...
        # Anchored at start-of-line (after optional indent + optional export).
        patterns = [
            rf"^[ \t]*(?:export\s+)?(?:const|let|var)\s+{re.escape(symbol)}(?:\s*:\s*[^=\n]+?)?\s*=\s*([^;\n]+?)(?=\s*(?:;|//|$|\n))",
        ]
    else:
        return False, f"unsupported language {lang.name!r}"

    for pat in patterns:
        m = re.search(pat, text, re.MULTILINE)
        if m:
            actual = m.group(1).strip()
            if _normalize_value(actual) == _normalize_value(expected):
                return True, f"{symbol} = {actual}"
            return False, f"expected {symbol} = {expected!r}, got {actual!r}"
    return False, f"symbol {symbol!r} not found"


def check_function(text: str, lang: LangAdapter, symbol: str, expected_sig: str) -> tuple[bool, str]:
    """Find function declaration and compare signature."""
    if lang.name == "go":
        # func Name(args) returns {
        pat = rf"\bfunc\s+{re.escape(symbol)}\s*(\([^\{{]+?)\s*\{{"
        m = re.search(pat, text, re.DOTALL)
        if not m:
            return False, f"function {symbol!r} not found"
        actual = m.group(1).strip()
        if _normalize_signature(actual) == _normalize_signature(expected_sig):
            return True, f"func {symbol}{actual}"
        return False, f"expected func {symbol}{expected_sig!r}, got func {symbol}{actual!r}"

    if lang.name == "python":
        # def name(args) -> return:   (return type optional)
        # [^)]* matches params including `aud: str` (the colon is OK inside parens).
        # Limitation: nested parens in default values (e.g. `(x=foo())`) not supported.
        pat = rf"^\s*def\s+{re.escape(symbol)}\s*(\([^)]*\))(?:\s*->\s*([^:\n]+?))?\s*:"
        m = re.search(pat, text, re.MULTILINE)
        if not m:
            return False, f"function {symbol!r} not found"
        params = m.group(1).strip()
        ret = (m.group(2) or "").strip()
        actual = params + (f" -> {ret}" if ret else "")
        if _normalize_signature(actual) == _normalize_signature(expected_sig):
            return True, f"def {symbol}{actual}"
        return False, f"expected def {symbol}{expected_sig!r}, got def {symbol}{actual!r}"

    if lang.name == "typescript":
        # function name(args): return {     or     (export const) name = (args): return =>     or     name(args): return =>
        pats = [
            rf"(?:export\s+)?(?:async\s+)?function\s+{re.escape(symbol)}\s*(\([^{{]+?)\s*\{{",
            rf"(?:export\s+)?(?:const|let)\s+{re.escape(symbol)}\s*=\s*(?:async\s*)?(\([^=]+?)\s*=>",
        ]
        for pat in pats:
            m = re.search(pat, text, re.DOTALL)
            if m:
                actual = m.group(1).strip()
                if _normalize_signature(actual) == _normalize_signature(expected_sig):
                    return True, f"function {symbol}{actual}"
                return False, f"expected function {symbol}{expected_sig!r}, got function {symbol}{actual!r}"
        return False, f"function {symbol!r} not found"

    return False, f"unsupported language {lang.name!r}"


def check_test(text: str, lang: LangAdapter, symbol: str) -> tuple[bool, str]:
    """Verify test exists AND is not skipped.

    Skipped tests pass the *structural* anchor but fail the *behavioral* one.
    They report as drift so CI doesn't go green on a t.Skip-stub.
    """
    if lang.name == "go":
        # Find the function start. We don't try to find the matching closing brace
        # (regex can't balance braces; a brace-counter would mishandle strings/comments).
        # Instead, the body is "everything from the opening brace up to the next top-level
        # `func` declaration (or EOF)". This is safe for `t.Skip` detection because Go's
        # convention places test functions at top level — no nested `func` shadowing.
        start_pat = rf"\bfunc\s+{re.escape(symbol)}\s*\([^)]*\)\s*\{{"
        m = re.search(start_pat, text)
        if not m:
            return False, f"test {symbol!r} not found"
        body_start = m.end()
        next_func = re.search(r"^func\s+", text[body_start:], re.MULTILINE)
        body_end = body_start + next_func.start() if next_func else len(text)
        body = text[body_start:body_end]
        if re.search(r"\bt\.Skip(?:Now)?\s*\(", body):
            return False, f"test {symbol!r} exists but is t.Skip-ped (structural only)"
        return True, f"test {symbol} present"

    if lang.name == "python":
        # find def test_foo(...): ... — capture body until next top-level def/class/EOF
        # also detect a @pytest.mark.skip decorator immediately above
        decorator_above = re.search(
            rf"(@pytest\.mark\.skip\b[^\n]*\n)\s*def\s+{re.escape(symbol)}\b",
            text,
        )
        if decorator_above:
            return False, f"test {symbol!r} exists but has @pytest.mark.skip"
        pat = rf"^\s*def\s+{re.escape(symbol)}\s*\([^)]*\)\s*:(.*?)(?=\n(?:def |class |@\w)|\Z)"
        m = re.search(pat, text, re.MULTILINE | re.DOTALL)
        if not m:
            return False, f"test {symbol!r} not found"
        body = m.group(1)
        if re.search(r"\b(?:pytest\.skip\s*\(|raise\s+SkipTest|self\.skipTest\s*\()", body):
            return False, f"test {symbol!r} exists but is skipped at runtime"
        return True, f"test {symbol} present"

    if lang.name == "typescript":
        # jest/vitest: test('name', ...) | it('name', ...) | test.skip(...) | xtest(...) | xit(...).
        # Single regex matches all variants and captures the prefix + optional modifier so we
        # can decide presence/skip from one pass.
        pat = (
            rf"\b(?P<prefix>test|it|xtest|xit)(?P<modifier>\.(?:skip|only))?"
            rf"\s*\(\s*['\"`]{re.escape(symbol)}['\"`]"
        )
        m = re.search(pat, text)
        if not m:
            return False, f"test {symbol!r} not found"
        prefix = m.group("prefix")
        modifier = m.group("modifier") or ""
        # xtest/xit are the skipped-by-default forms; .skip is explicit
        if prefix in ("xtest", "xit") or modifier == ".skip":
            return False, f"test {symbol!r} exists but is skipped ({prefix}{modifier})"
        return True, f"test {symbol} present"

    return False, f"unsupported language {lang.name!r}"


# ---------------------------------------------------------------------------
# Per-anchor dispatch
# ---------------------------------------------------------------------------


def check_anchor(anchor: Anchor, repo_root: Path, spec_id: str) -> DriftResult:
    """Verify a single anchor against its target file."""
    if anchor.kind == "manual":
        return DriftResult(spec_id, anchor.id, "manual", f"manual — verify externally ({anchor.file})")

    target = repo_root / anchor.file
    if not target.exists():
        return DriftResult(spec_id, anchor.id, "missing", f"file not found: {anchor.file}")

    lang = detect_language(anchor.file)
    if lang is None:
        return DriftResult(spec_id, anchor.id, "missing", f"unsupported extension: {anchor.file}")

    text = target.read_text()

    if anchor.kind == "const":
        ok, detail = check_const(text, lang, anchor.symbol, anchor.value)
    elif anchor.kind == "function":
        ok, detail = check_function(text, lang, anchor.symbol, anchor.signature)
    elif anchor.kind == "test":
        ok, detail = check_test(text, lang, anchor.symbol)
    else:
        return DriftResult(spec_id, anchor.id, "drift", f"unknown kind {anchor.kind!r}")

    return DriftResult(spec_id, anchor.id, "ok" if ok else "drift", detail)


# ---------------------------------------------------------------------------
# Git-diff filter
# ---------------------------------------------------------------------------


def changed_files(repo_root: Path) -> set[str] | None:
    """Return the set of files changed in the working tree, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            # Fall back to working-tree-only diff
            result = subprocess.run(
                ["git", "-C", str(repo_root), "diff", "--name-only"],
                capture_output=True, text=True, timeout=5,
            )
        return set(filter(None, result.stdout.strip().split("\n")))
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_check(
    knowledge_root: Path,
    repo_root: Path,
    only_spec: str | None = None,
    only_changed: bool = False,
    verbose: bool = False,
) -> tuple[int, list[DriftResult]]:
    """Library entry point: return (exit_code, results)."""
    specs: list[Spec] = []
    for spec_path in find_specs(knowledge_root):
        spec = Spec.load(spec_path)
        if only_spec and spec.spec_id != only_spec:
            continue
        if spec.anchors:
            specs.append(spec)

    if not specs:
        return 0, []

    changed = changed_files(repo_root) if only_changed else None

    results: list[DriftResult] = []
    for spec in specs:
        errs = spec.validate()
        for err in errs:
            results.append(DriftResult(spec.spec_id, "_validation", "drift", err))
        for anchor in spec.anchors:
            if changed is not None and anchor.file not in changed:
                continue
            results.append(check_anchor(anchor, repo_root, spec.spec_id))

    drifts = [r for r in results if r.kind in ("drift", "missing")]
    return (1 if drifts else 0), results


def main():
    parser = argparse.ArgumentParser(description="KEEP /keep-check-drift — deterministic anchor verifier")
    parser.add_argument("--knowledge", default="knowledge", help="Path to /knowledge/ root (default: ./knowledge)")
    parser.add_argument("--repo", default=None, help="Repo root (default: parent of --knowledge)")
    parser.add_argument("--spec", help="Check only this spec id (default: all)")
    parser.add_argument("--changed", action="store_true", help="Only check anchors whose target file appears in git diff")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show OK results too")
    args = parser.parse_args()

    knowledge_root = Path(args.knowledge).resolve()
    if not knowledge_root.exists():
        print(f"error: {knowledge_root} does not exist", file=sys.stderr)
        sys.exit(2)
    repo_root = Path(args.repo).resolve() if args.repo else knowledge_root.parent

    exit_code, results = run_check(
        knowledge_root=knowledge_root,
        repo_root=repo_root,
        only_spec=args.spec,
        only_changed=args.changed,
        verbose=args.verbose,
    )

    drifts = [r for r in results if r.kind in ("drift", "missing")]
    oks = [r for r in results if r.kind == "ok"]
    manuals = [r for r in results if r.kind == "manual"]

    # Print report
    if not results:
        print("no anchored specs found in /knowledge — nothing to check")
        sys.exit(0)

    if args.verbose:
        print(f"=== KEEP /keep-check-drift — {len(results)} anchors evaluated ===")
        for r in results:
            print(f"  [{r.marker:7s}] {r.spec_id}::{r.anchor_id}  — {r.detail}")
        print(f"\nsummary: {len(oks)} ok / {len(drifts)} drift / {len(manuals)} manual")
    elif drifts:
        print(f"DRIFT detected — {len(drifts)} anchor(s):")
        for r in drifts:
            print(f"  [{r.marker}] {r.spec_id}::{r.anchor_id}  — {r.detail}")
        print(f"\n({len(oks)} ok, {len(manuals)} manual)")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
