"""Shared utilities for KEEP scripts (anchor / stub / check-drift).

Single source of truth for:
- Spec frontmatter parsing (YAML)
- Anchor schema validation
- Marker grammar (// keep:anchor SPEC-id::anchor-id  ...  // keep:end)
- Per-language adapters (comment style, projection idioms)
- File scanning and ID resolution

Kept deliberately stdlib-only — no PyYAML dependency. The frontmatter is a
small, well-defined subset of YAML that we parse with a hand-rolled tokenizer.
This keeps the skill installable without a virtualenv.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Spec frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a markdown file into (frontmatter_dict, body).

    Returns ({}, text) if no frontmatter present.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    yaml_text = m.group(1)
    body = text[m.end():]
    return _parse_yaml(yaml_text), body


def _parse_yaml(text: str) -> dict:
    """Minimal YAML parser sufficient for KEEP frontmatter.

    Supports:
    - top-level key: value pairs (strings, ints, lists in [a, b] form)
    - list-of-dicts under a key:
          anchors:
            - id: foo
              kind: const
              ...
    - quoted strings ("..." or '...') with embedded colons preserved
    - inline list values: tags: [auth, jwt, security]

    Does NOT support: nested dicts beyond list-of-dicts, multiline strings,
    anchors/aliases, or any YAML 1.2 obscurities. KEEP frontmatter does not
    need them.
    """
    result: dict = {}
    current_list_key: str | None = None
    current_dict: dict | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        # List item under a key (starts with "  - ")
        stripped = raw_line.lstrip()
        if stripped.startswith("- ") and current_list_key is not None:
            # Start of new dict in list
            current_dict = {}
            result[current_list_key].append(current_dict)
            rest = stripped[2:]
            if ":" in rest:
                k, v = _split_kv(rest)
                current_dict[k] = v
            continue

        # Continuation of dict in list (indented "  key: value")
        if raw_line.startswith(" ") and current_dict is not None:
            if ":" in stripped:
                k, v = _split_kv(stripped)
                current_dict[k] = v
            continue

        # Top-level key
        if ":" in stripped:
            k, v = _split_kv(stripped)
            if v == "":
                # Empty value → expect list under it
                result[k] = []
                current_list_key = k
                current_dict = None
            else:
                result[k] = v
                current_list_key = None
                current_dict = None

    return result


def _split_kv(line: str) -> tuple[str, object]:
    """Split 'key: value' respecting quoted values and inline lists."""
    colon = line.index(":")
    key = line[:colon].strip()
    value = line[colon + 1:].strip()

    if value == "":
        return key, ""
    if value.startswith('"') and value.endswith('"'):
        return key, value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return key, value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        items = [x.strip().strip('"').strip("'") for x in value[1:-1].split(",")]
        return key, [x for x in items if x]
    # Numeric
    try:
        return key, int(value)
    except ValueError:
        return key, value


def serialize_frontmatter(data: dict) -> str:
    """Inverse of parse_frontmatter — emit YAML the same parser can re-read."""
    lines = ["---"]
    for k, v in data.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            lines.append(f"{k}:")
            for item in v:
                first = True
                for ik, iv in item.items():
                    prefix = "  - " if first else "    "
                    first = False
                    lines.append(f"{prefix}{ik}: {_yaml_value(iv)}")
        elif isinstance(v, list):
            inner = ", ".join(_yaml_value(x) for x in v)
            lines.append(f"{k}: [{inner}]")
        else:
            lines.append(f"{k}: {_yaml_value(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _yaml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = str(v)
    # Quote if contains special characters or starts with special char
    if any(c in s for c in [":", "#", "[", "]", "{", "}", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`"]) or s != s.strip():
        return f'"{s}"'
    return s


# ---------------------------------------------------------------------------
# Anchor schema
# ---------------------------------------------------------------------------


VALID_KINDS = {"const", "function", "test", "manual"}


@dataclass
class Anchor:
    """A single anchor entry from a spec's frontmatter."""

    id: str
    claim: str
    kind: str
    file: str
    symbol: str = ""
    value: str = ""
    signature: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Anchor":
        def s(k: str) -> str:
            """Coerce frontmatter value to string. The YAML parser may infer ints."""
            v = d.get(k, "")
            return "" if v is None else str(v)
        return cls(
            id=s("id"),
            claim=s("claim"),
            kind=s("kind"),
            file=s("file"),
            symbol=s("symbol"),
            value=s("value"),
            signature=s("signature"),
            notes=s("notes"),
        )

    def validate(self) -> list[str]:
        errs = []
        if not self.id:
            errs.append("missing id")
        if not self.claim:
            errs.append(f"anchor {self.id!r}: missing claim")
        if self.kind not in VALID_KINDS:
            errs.append(f"anchor {self.id!r}: kind {self.kind!r} not in {VALID_KINDS}")
        if not self.file:
            errs.append(f"anchor {self.id!r}: missing file")
        if self.kind == "const" and (not self.symbol or not self.value):
            errs.append(f"anchor {self.id!r}: kind=const requires symbol and value")
        if self.kind == "function" and (not self.symbol or not self.signature):
            errs.append(f"anchor {self.id!r}: kind=function requires symbol and signature")
        if self.kind == "test" and not self.symbol:
            errs.append(f"anchor {self.id!r}: kind=test requires symbol (test function name)")
        return errs


@dataclass
class Spec:
    """A spec file loaded from disk, with its anchors parsed out."""

    spec_id: str
    path: Path
    frontmatter: dict
    body: str
    anchors: list[Anchor] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Spec":
        text = path.read_text()
        fm, body = parse_frontmatter(text)
        spec_id = fm.get("id", path.stem)
        raw_anchors = fm.get("anchors", []) or []
        anchors = [Anchor.from_dict(a) for a in raw_anchors]
        return cls(spec_id=spec_id, path=path, frontmatter=fm, body=body, anchors=anchors)

    def validate(self) -> list[str]:
        errs = []
        for a in self.anchors:
            errs.extend(a.validate())
        # Check for duplicate anchor ids
        ids = [a.id for a in self.anchors]
        for dup in {x for x in ids if ids.count(x) > 1}:
            errs.append(f"duplicate anchor id: {dup!r}")
        return errs

    def anchors_by_file(self) -> dict[str, list[Anchor]]:
        """Group anchors by target file path."""
        groups: dict[str, list[Anchor]] = {}
        for a in self.anchors:
            groups.setdefault(a.file, []).append(a)
        return groups


# ---------------------------------------------------------------------------
# Language adapters
# ---------------------------------------------------------------------------


@dataclass
class LangAdapter:
    """Per-language conventions for marker grammar and projection idioms."""

    name: str
    extensions: tuple[str, ...]
    line_comment: str  # "//" or "#"
    imports_header: str = ""   # auto-emitted at the top of a generated file


GO = LangAdapter(name="go", extensions=(".go",), line_comment="//")
PYTHON = LangAdapter(name="python", extensions=(".py",), line_comment="#")
TS = LangAdapter(name="typescript", extensions=(".ts", ".tsx"), line_comment="//")

ALL_LANGS = (GO, PYTHON, TS)


def detect_language(file_path: str) -> LangAdapter | None:
    """Pick an adapter by file extension. Returns None for unsupported."""
    suffix = Path(file_path).suffix.lower()
    for lang in ALL_LANGS:
        if suffix in lang.extensions:
            return lang
    return None


# ---------------------------------------------------------------------------
# Marker grammar
# ---------------------------------------------------------------------------


def marker_start(lang: LangAdapter, spec_id: str, anchor_id: str) -> str:
    return f"{lang.line_comment} keep:anchor {spec_id}::{anchor_id}"


def marker_end(lang: LangAdapter) -> str:
    return f"{lang.line_comment} keep:end"


def _marker_regex(lang: LangAdapter) -> re.Pattern:
    # Match the comment prefix (// or #) and the keep:anchor / keep:end pattern.
    prefix = re.escape(lang.line_comment)
    return re.compile(
        rf"^[ \t]*{prefix}\s*keep:anchor\s+([\w\-]+)::([\w\-]+)\s*$"
        rf"(.*?)"
        rf"^[ \t]*{prefix}\s*keep:end\s*$",
        re.MULTILINE | re.DOTALL,
    )


@dataclass
class MarkerRegion:
    """A discovered keep:anchor block in an existing file."""

    spec_id: str
    anchor_id: str
    content: str        # content between marker lines (without the markers themselves)
    start: int          # char offset in the file where the START marker line begins
    end: int            # char offset where the END marker line ends


def find_markers(text: str, lang: LangAdapter) -> list[MarkerRegion]:
    """Scan a file's text for all keep:anchor blocks."""
    regions: list[MarkerRegion] = []
    for m in _marker_regex(lang).finditer(text):
        regions.append(MarkerRegion(
            spec_id=m.group(1),
            anchor_id=m.group(2),
            content=m.group(3),
            start=m.start(),
            end=m.end(),
        ))
    return regions


def render_region(lang: LangAdapter, spec_id: str, anchor: Anchor, body: str) -> str:
    """Wrap a body in marker fences. body should be the projected code lines."""
    start = marker_start(lang, spec_id, anchor.id)
    end = marker_end(lang)
    # Body is single-newline-terminated; ensure it.
    if not body.endswith("\n"):
        body = body + "\n"
    return f"{start}\n{body}{end}\n"


# ---------------------------------------------------------------------------
# Spec discovery
# ---------------------------------------------------------------------------


def find_specs(knowledge_root: Path) -> Iterator[Path]:
    """Yield all spec/ADR/idea files under /knowledge that have frontmatter."""
    for path in knowledge_root.rglob("*.md"):
        if path.name == "INDEX.md":
            continue
        try:
            head = path.read_text()[:512]
        except OSError:
            continue
        if head.startswith("---\n"):
            yield path


def load_spec_by_id(knowledge_root: Path, spec_id: str) -> Spec | None:
    """Find a spec by its frontmatter id and load it."""
    for path in find_specs(knowledge_root):
        spec = Spec.load(path)
        if spec.spec_id == spec_id:
            return spec
    return None
