"""Parse vLLM serve CLI flags from `vllm serve --help` (cached)."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass

_FLAG_LINE = re.compile(r"^\s{2,}(--[\w][\w.-]*)(?:\s+([A-Z][A-Z_]+))?\s*$")
_CACHE_TTL_S = 3600.0

_cache: list[dict] | None = None
_cache_at: float = 0.0


@dataclass(frozen=True)
class VllmFlag:
    cli_name: str
    yaml_name: str
    consumes_value: bool
    arg_hint: str | None = None


def _cli_to_yaml_name(cli_name: str) -> str:
    name = cli_name.lstrip("-")
    if "." in name:
        return name
    return name.replace("-", "_")


def _parse_help(text: str) -> list[VllmFlag]:
    flags: list[VllmFlag] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = _FLAG_LINE.match(line)
        if not m:
            continue
        cli = m.group(1)
        if cli in seen:
            continue
        seen.add(cli)
        arg_hint = m.group(2)
        flags.append(
            VllmFlag(
                cli_name=cli,
                yaml_name=_cli_to_yaml_name(cli),
                consumes_value=arg_hint is not None,
                arg_hint=arg_hint,
            )
        )
    return flags


def _fetch_help_text() -> str:
    try:
        proc = subprocess.run(
            ["vllm", "serve", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("vllm executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("vllm serve --help timed out") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(err or f"vllm serve --help exited {proc.returncode}")
    return proc.stdout or ""


def list_vllm_flags(*, refresh: bool = False) -> list[dict]:
    """Return cached flag metadata for UI autocomplete."""
    global _cache, _cache_at
    now = time.monotonic()
    if not refresh and _cache is not None and (now - _cache_at) < _CACHE_TTL_S:
        return _cache

    text = _fetch_help_text()
    flags = _parse_help(text)
    _cache = [
        {
            "cli_name": f.cli_name,
            "yaml_name": f.yaml_name,
            "consumes_value": f.consumes_value,
            "arg_hint": f.arg_hint,
        }
        for f in flags
    ]
    _cache_at = now
    return _cache
