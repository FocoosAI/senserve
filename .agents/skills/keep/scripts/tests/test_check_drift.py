#!/usr/bin/env python3
"""Deterministic test suite for /keep-check-drift.

Each test:
  1. Creates a temp repo with /knowledge/<spec>.md + a source file.
  2. Runs check_drift.py via subprocess.
  3. Asserts on exit code and on the result list returned by the library API.

No LLM. No subagents. Pure assertions on the deterministic script's behavior.

Run with: python3 test_check_drift.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

# Make _keep and check_drift importable when running this file directly
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from check_drift import run_check  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_spec(knowledge_root: Path, spec_id: str, anchors: list[dict],
              path_in_knowledge: str = "docs/specs/test.md") -> Path:
    """Write a minimal spec file with the given anchors. Returns the file path."""
    full = knowledge_root / path_in_knowledge
    full.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        "---",
        f"id: {spec_id}",
        f'title: "{spec_id}"',
        "status: accepted",
        "type: spec",
        "domain: test",
        "anchors:",
    ]
    for a in anchors:
        first = True
        for k, v in a.items():
            prefix = "  - " if first else "    "
            first = False
            # Quote strings to be safe across our hand-rolled YAML parser.
            # If the value itself contains a double quote, wrap in single quotes
            # to preserve the inner literal.
            if isinstance(v, str) and '"' in v:
                fm_lines.append(f"{prefix}{k}: '{v}'")
            elif isinstance(v, str) and any(c in v for c in [":", "*", "(", ")", " ", "|", "[", "]"]):
                fm_lines.append(f'{prefix}{k}: "{v}"')
            else:
                fm_lines.append(f"{prefix}{k}: {v}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append("# spec body")
    full.write_text("\n".join(fm_lines))
    return full


def make_code(repo_root: Path, rel_path: str, content: str) -> Path:
    """Write a source file under repo_root/rel_path. Returns the path."""
    full = repo_root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(textwrap.dedent(content).lstrip("\n"))
    return full


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class BaseTest(unittest.TestCase):
    """Base class that creates a temp repo + knowledge dir for each test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = Path(self.tmp)
        self.knowledge = self.repo / "knowledge"
        self.knowledge.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self) -> tuple[int, list]:
        return run_check(self.knowledge, self.repo)


class TestNoAnchors(BaseTest):
    """Smoke: no specs with anchors → exit 0, empty results."""

    def test_empty_knowledge(self):
        code, results = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(results, [])

    def test_spec_without_anchors(self):
        # spec with no anchors block — should be ignored
        (self.knowledge / "docs" / "specs").mkdir(parents=True)
        (self.knowledge / "docs" / "specs" / "x.md").write_text(
            "---\nid: SPEC-x\nstatus: accepted\ntype: spec\n---\n\nbody\n"
        )
        code, results = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(results, [])


# ---------------- GO ----------------------------------------------------------


class TestGoConst(BaseTest):

    def test_const_matches(self):
        make_spec(self.knowledge, "SPEC-go-ttl", [
            {"id": "ttl", "claim": "TTL is 5min", "kind": "const",
             "file": "main.go", "symbol": "TOKEN_TTL", "value": "5 * time.Minute"},
        ])
        make_code(self.repo, "main.go", """
            package main
            import "time"
            const TOKEN_TTL = 5 * time.Minute
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")
        self.assertEqual(results[0].kind, "ok")

    def test_const_drifts(self):
        make_spec(self.knowledge, "SPEC-go-ttl", [
            {"id": "ttl", "claim": "TTL is 5min", "kind": "const",
             "file": "main.go", "symbol": "TOKEN_TTL", "value": "5 * time.Minute"},
        ])
        make_code(self.repo, "main.go", """
            package main
            import "time"
            const TOKEN_TTL = 30 * time.Minute
        """)
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(results[0].kind, "drift")
        self.assertIn("30 * time.Minute", results[0].detail)

    def test_const_whitespace_tolerant(self):
        """`5*time.Minute` should match `5 * time.Minute` (collapsed whitespace)."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "ttl", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "X", "value": "5 * time.Minute"},
        ])
        make_code(self.repo, "main.go", "const X    =    5*time.Minute\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_const_missing_symbol(self):
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "ttl", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "TOKEN_TTL", "value": "5"},
        ])
        make_code(self.repo, "main.go", "package main\n// no const here\n")
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(results[0].kind, "drift")
        self.assertIn("not found", results[0].detail)

    def test_const_with_type_annotation(self):
        """`const X time.Duration = 5*time.Minute` — Go's typed const form."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "ttl", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "TOKEN_TTL", "value": "5 * time.Minute"},
        ])
        make_code(self.repo, "main.go",
                  "const TOKEN_TTL time.Duration = 5 * time.Minute\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")


class TestGoFunction(BaseTest):

    def test_function_matches(self):
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "sig", "claim": "x", "kind": "function",
             "file": "main.go", "symbol": "NewToken",
             "signature": "(aud string) (string, error)"},
        ])
        make_code(self.repo, "main.go", """
            package main

            func NewToken(aud string) (string, error) {
                return "", nil
            }
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")
        self.assertEqual(results[0].kind, "ok")

    def test_function_signature_drifts(self):
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "sig", "claim": "x", "kind": "function",
             "file": "main.go", "symbol": "NewToken",
             "signature": "(aud string) (string, error)"},
        ])
        make_code(self.repo, "main.go", """
            package main
            func NewToken(aud string, ttl int) (string, error) {
                return "", nil
            }
        """)
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(results[0].kind, "drift")
        self.assertIn("ttl int", results[0].detail)

    def test_function_missing(self):
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "sig", "claim": "x", "kind": "function",
             "file": "main.go", "symbol": "DoesNotExist",
             "signature": "()"},
        ])
        make_code(self.repo, "main.go", "package main\n")
        code, results = self.check()
        self.assertEqual(code, 1)


class TestGoTest(BaseTest):

    def test_test_present_and_not_skipped(self):
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "main_test.go", "symbol": "TestNewToken"},
        ])
        make_code(self.repo, "main_test.go", """
            package main
            import "testing"
            func TestNewToken(t *testing.T) {
                if 1+1 != 2 {
                    t.Fatal("math broke")
                }
            }
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_test_present_but_skipped(self):
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "main_test.go", "symbol": "TestNewToken"},
        ])
        make_code(self.repo, "main_test.go", """
            package main
            import "testing"
            func TestNewToken(t *testing.T) {
                t.Skip("not yet")
            }
        """)
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertIn("Skip", results[0].detail)

    def test_test_missing(self):
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "main_test.go", "symbol": "TestMissing"},
        ])
        make_code(self.repo, "main_test.go",
                  "package main\nimport \"testing\"\nfunc TestOther(t *testing.T) {}\n")
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertIn("not found", results[0].detail)


# ---------------- PYTHON ------------------------------------------------------


class TestPythonConst(BaseTest):

    def test_const_matches(self):
        make_spec(self.knowledge, "SPEC-py", [
            {"id": "ttl", "claim": "x", "kind": "const",
             "file": "auth.py", "symbol": "TOKEN_TTL", "value": "timedelta(minutes=5)"},
        ])
        make_code(self.repo, "auth.py", """
            from datetime import timedelta
            TOKEN_TTL = timedelta(minutes=5)
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_const_drifts(self):
        make_spec(self.knowledge, "SPEC-py", [
            {"id": "ttl", "claim": "x", "kind": "const",
             "file": "auth.py", "symbol": "TOKEN_TTL", "value": "timedelta(minutes=5)"},
        ])
        make_code(self.repo, "auth.py", "TOKEN_TTL = timedelta(minutes=30)\n")
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertIn("minutes=30", results[0].detail)

    def test_const_with_type_annotation(self):
        make_spec(self.knowledge, "SPEC-py", [
            {"id": "ttl", "claim": "x", "kind": "const",
             "file": "auth.py", "symbol": "TOKEN_TTL", "value": "300"},
        ])
        make_code(self.repo, "auth.py",
                  "from typing import Final\nTOKEN_TTL: Final[int] = 300\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")


class TestPythonFunction(BaseTest):

    def test_function_matches(self):
        make_spec(self.knowledge, "SPEC-py", [
            {"id": "sig", "claim": "x", "kind": "function",
             "file": "auth.py", "symbol": "new_token",
             "signature": "(aud: str) -> tuple[str, Exception | None]"},
        ])
        make_code(self.repo, "auth.py", """
            def new_token(aud: str) -> tuple[str, Exception | None]:
                return "", None
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_function_no_return_annotation(self):
        """Signature without -> is also valid."""
        make_spec(self.knowledge, "SPEC-py", [
            {"id": "sig", "claim": "x", "kind": "function",
             "file": "auth.py", "symbol": "new_token", "signature": "(aud: str)"},
        ])
        make_code(self.repo, "auth.py", "def new_token(aud: str):\n    return ''\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")


class TestPythonTest(BaseTest):

    def test_test_present_and_not_skipped(self):
        make_spec(self.knowledge, "SPEC-py", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "test_auth.py", "symbol": "test_new_token"},
        ])
        make_code(self.repo, "test_auth.py", """
            def test_new_token():
                assert 1 + 1 == 2
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_test_with_pytest_skip_decorator(self):
        make_spec(self.knowledge, "SPEC-py", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "test_auth.py", "symbol": "test_new_token"},
        ])
        make_code(self.repo, "test_auth.py", """
            import pytest

            @pytest.mark.skip(reason="not yet")
            def test_new_token():
                assert 1 + 1 == 2
        """)
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertIn("skip", results[0].detail.lower())

    def test_test_with_runtime_skip(self):
        make_spec(self.knowledge, "SPEC-py", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "test_auth.py", "symbol": "test_new_token"},
        ])
        make_code(self.repo, "test_auth.py", """
            import pytest
            def test_new_token():
                pytest.skip("not yet")
        """)
        code, results = self.check()
        self.assertEqual(code, 1)


# ---------------- TYPESCRIPT --------------------------------------------------


class TestTypeScript(BaseTest):

    def test_const_matches(self):
        make_spec(self.knowledge, "SPEC-ts", [
            {"id": "ttl", "claim": "x", "kind": "const",
             "file": "auth.ts", "symbol": "TOKEN_TTL", "value": "300"},
        ])
        make_code(self.repo, "auth.ts", "export const TOKEN_TTL: number = 300;\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_const_drifts(self):
        make_spec(self.knowledge, "SPEC-ts", [
            {"id": "ttl", "claim": "x", "kind": "const",
             "file": "auth.ts", "symbol": "TOKEN_TTL", "value": "300"},
        ])
        make_code(self.repo, "auth.ts", "export const TOKEN_TTL: number = 900;\n")
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertIn("900", results[0].detail)

    def test_function_declaration(self):
        make_spec(self.knowledge, "SPEC-ts", [
            {"id": "sig", "claim": "x", "kind": "function",
             "file": "auth.ts", "symbol": "newToken",
             "signature": "(aud: string): [string, Error | null]"},
        ])
        make_code(self.repo, "auth.ts", """
            export function newToken(aud: string): [string, Error | null] {
                return ["", null];
            }
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_arrow_function(self):
        make_spec(self.knowledge, "SPEC-ts", [
            {"id": "sig", "claim": "x", "kind": "function",
             "file": "auth.ts", "symbol": "newToken",
             "signature": "(aud: string): string"},
        ])
        make_code(self.repo, "auth.ts",
                  "export const newToken = (aud: string): string => aud;\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_test_jest_style_present(self):
        make_spec(self.knowledge, "SPEC-ts", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "auth.test.ts", "symbol": "issues a token"},
        ])
        make_code(self.repo, "auth.test.ts", """
            test('issues a token', () => {
                expect(1 + 1).toBe(2);
            });
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_test_jest_style_skipped(self):
        make_spec(self.knowledge, "SPEC-ts", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "auth.test.ts", "symbol": "issues a token"},
        ])
        make_code(self.repo, "auth.test.ts",
                  "test.skip('issues a token', () => { expect(true).toBe(true); });\n")
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertIn("skip", results[0].detail.lower())


# ---------------- MANUAL & MIXED ---------------------------------------------


class TestManualAnchor(BaseTest):

    def test_manual_anchor_reported_but_does_not_fail(self):
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "rot", "claim": "Rotation is every 90 days", "kind": "manual",
             "file": "infra/k8s/secrets/cronjob.yaml"},
        ])
        # Don't create the file — manual means we don't enforce it
        code, results = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(results[0].kind, "manual")


class TestMixedAnchors(BaseTest):

    def test_some_pass_some_drift(self):
        make_spec(self.knowledge, "SPEC-mix", [
            {"id": "ttl", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "TTL", "value": "5"},
            {"id": "iss", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "ISS", "value": "\"auth-service\""},
        ])
        make_code(self.repo, "main.go", """
            package main
            const TTL = 5
            const ISS = "other-service"
        """)
        code, results = self.check()
        self.assertEqual(code, 1)
        # First passes, second drifts
        oks = [r for r in results if r.kind == "ok"]
        drifts = [r for r in results if r.kind == "drift"]
        self.assertEqual(len(oks), 1)
        self.assertEqual(len(drifts), 1)
        self.assertEqual(drifts[0].anchor_id, "iss")


class TestMissingFile(BaseTest):

    def test_target_file_missing(self):
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "t", "claim": "x", "kind": "const",
             "file": "does_not_exist.go", "symbol": "X", "value": "5"},
        ])
        # No file created
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(results[0].kind, "missing")


class TestSpecFilter(BaseTest):

    def test_only_checks_specified_spec(self):
        # Two specs; one drifted, the other ok. Filter to the ok one.
        make_spec(self.knowledge, "SPEC-good", [
            {"id": "a", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "A", "value": "1"},
        ], path_in_knowledge="docs/specs/good.md")
        make_spec(self.knowledge, "SPEC-bad", [
            {"id": "b", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "B", "value": "2"},
        ], path_in_knowledge="docs/specs/bad.md")
        make_code(self.repo, "main.go", "const A = 1\nconst B = 99\n")
        code, results = run_check(self.knowledge, self.repo, only_spec="SPEC-good")
        self.assertEqual(code, 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].spec_id, "SPEC-good")


class TestValidationErrors(BaseTest):

    def test_invalid_kind_surfaces_validation_error(self):
        make_spec(self.knowledge, "SPEC-bad", [
            {"id": "x", "claim": "x", "kind": "wrong",
             "file": "main.go"},
        ])
        make_code(self.repo, "main.go", "package main\n")
        code, results = self.check()
        self.assertEqual(code, 1)
        # First result is the validation error
        self.assertTrue(any(r.anchor_id == "_validation" for r in results))


# ---------------- ADVERSARIAL / EDGE CASES -----------------------------------


class TestAdversarial(BaseTest):
    """Edge cases that exercise the regex matchers' limits."""

    def test_similar_symbol_does_not_false_match(self):
        """Anchor on `TTL` must NOT match `TTL_DEFAULT` (longer prefix)."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "a", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "TTL", "value": "5"},
        ])
        make_code(self.repo, "main.go", "const TTL_DEFAULT = 999\nconst TTL = 5\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_const_in_function_body_not_matched(self):
        """A `const TTL = 5` inside a function body (Go const-decl block) should still match.

        But more importantly: a same-named local variable should not preempt a top-level decl."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "a", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "TTL", "value": "5"},
        ])
        make_code(self.repo, "main.go", """
            package main
            func init() {
                local := 99  // shadow, should be ignored
                _ = local
            }
            const TTL = 5
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_commented_out_declaration_matched_loosely(self):
        """A commented-out `// const TTL = 5` line — current matcher will see the value
        but also see the real value. As long as the real one matches, we pass.

        This documents that the regex is line-based and commented declarations are
        a known limitation (Phase B AST parsing would fix it)."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "a", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "TTL", "value": "10"},
        ])
        # Only commented decl + real decl with different value → real one is matched
        make_code(self.repo, "main.go", """
            package main
            // const TTL = 5
            const TTL = 10
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_string_value_with_quotes(self):
        """A const with a string literal value (including the quotes) matches exactly."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "iss", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "ISSUER",
             "value": '"auth-service"'},
        ])
        make_code(self.repo, "main.go", 'const ISSUER = "auth-service"\n')
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_value_with_negative_number(self):
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "n", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "DELTA", "value": "-7"},
        ])
        make_code(self.repo, "main.go", "const DELTA = -7\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_python_const_with_complex_expression(self):
        make_spec(self.knowledge, "SPEC-py", [
            {"id": "max", "claim": "x", "kind": "const",
             "file": "x.py", "symbol": "MAX_TOKENS",
             "value": "1024 * 4"},
        ])
        make_code(self.repo, "x.py", "MAX_TOKENS = 1024 * 4\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_multiple_specs_target_same_file(self):
        """Two anchors from two different specs both target main.go. Both should be checked."""
        make_spec(self.knowledge, "SPEC-a", [
            {"id": "x", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "A", "value": "1"},
        ], path_in_knowledge="docs/specs/a.md")
        make_spec(self.knowledge, "SPEC-b", [
            {"id": "y", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "B", "value": "2"},
        ], path_in_knowledge="docs/specs/b.md")
        make_code(self.repo, "main.go", "const A = 1\nconst B = 2\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")
        spec_ids = {r.spec_id for r in results}
        self.assertEqual(spec_ids, {"SPEC-a", "SPEC-b"})

    def test_function_with_multiline_signature(self):
        """Go function with arguments wrapping to multiple lines."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "sig", "claim": "x", "kind": "function",
             "file": "main.go", "symbol": "Process",
             "signature": "(in []byte, opts Options) (Result, error)"},
        ])
        make_code(self.repo, "main.go", """
            package main
            func Process(
                in []byte,
                opts Options,
            ) (Result, error) {
                return Result{}, nil
            }
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_anchor_on_test_file_with_other_tests(self):
        """test_check_drift looking for one specific test among many."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "main_test.go", "symbol": "TestFoo"},
        ])
        make_code(self.repo, "main_test.go", """
            package main
            import "testing"
            func TestBar(t *testing.T) {}
            func TestFoo(t *testing.T) { _ = 1 }
            func TestBaz(t *testing.T) { t.Skip("not yet") }
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_python_test_followed_by_class_does_not_leak(self):
        """The python test-body capture must STOP at the next def/class, not consume forever."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "test_x.py", "symbol": "test_alpha"},
        ])
        make_code(self.repo, "test_x.py", """
            def test_alpha():
                assert True

            def test_beta():
                pytest.skip("not yet")   # this skip must NOT leak into test_alpha's body
        """)
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_typescript_test_only_modifier_is_not_skip(self):
        """test.only is the opposite of skip — should pass."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "t", "claim": "x", "kind": "test",
             "file": "x.test.ts", "symbol": "alpha"},
        ])
        make_code(self.repo, "x.test.ts",
                  "test.only('alpha', () => { expect(true).toBe(true); });\n")
        code, results = self.check()
        self.assertEqual(code, 0, f"results: {results}")

    def test_unsupported_language_reports_missing(self):
        """Anchor pointing to a .rs file → 'missing' with explanation."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "c", "claim": "x", "kind": "const",
             "file": "lib.rs", "symbol": "X", "value": "5"},
        ])
        make_code(self.repo, "lib.rs", "const X: u32 = 5;\n")
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(results[0].kind, "missing")
        self.assertIn("unsupported", results[0].detail)

    def test_validation_failure_blocks_when_only_one_spec(self):
        """A spec with malformed anchor (missing required field) — surfaces validation error."""
        make_spec(self.knowledge, "SPEC-x", [
            {"id": "c", "claim": "x", "kind": "const",
             "file": "main.go", "symbol": "X"},  # missing required `value` for kind=const
        ])
        make_code(self.repo, "main.go", "const X = 5\n")
        code, results = self.check()
        self.assertEqual(code, 1)
        self.assertTrue(any(r.anchor_id == "_validation" for r in results))


# ---------------------------------------------------------------------------


def run_all():
    """Run all tests with summary output."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestNoAnchors,
        TestGoConst, TestGoFunction, TestGoTest,
        TestPythonConst, TestPythonFunction, TestPythonTest,
        TestTypeScript,
        TestManualAnchor, TestMixedAnchors, TestMissingFile,
        TestSpecFilter, TestValidationErrors,
        TestAdversarial,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_all())
