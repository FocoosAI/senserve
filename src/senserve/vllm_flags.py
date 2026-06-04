"""vLLM serve CLI flags parsed once from `vllm serve --help` at backend startup."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_FLAG_LINE = re.compile(r"^\s{2,}(--[\w][\w.-]*)(?:\s+([A-Z][A-Z_]+))?\s*$")

# Populated by preload_at_startup() before the gateway serves traffic.
_vllm_flags: list[dict] | None = None


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


def _flags_to_dicts(flags: list[VllmFlag]) -> list[dict]:
    return [
        {
            "cli_name": f.cli_name,
            "yaml_name": f.yaml_name,
            "consumes_value": f.consumes_value,
            "arg_hint": f.arg_hint,
        }
        for f in flags
    ]


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


def is_preloaded() -> bool:
    return _vllm_flags is not None


def preload_at_startup() -> None:
    """Run `vllm serve --help` and store parsed flags in ``_vllm_flags``."""
    global _vllm_flags
    text = _fetch_help_text()
    parsed = _parse_help(text)
    _vllm_flags = _flags_to_dicts(parsed)
    logger.info("Preloaded %d vLLM serve flags from --help", len(_vllm_flags))


def list_vllm_flags(*, refresh: bool = False) -> list[dict]:
    """Return startup-cached flag metadata; optional refresh re-runs --help."""
    global _vllm_flags
    if refresh:
        preload_at_startup()
    if _vllm_flags is None:
        raise RuntimeError("vLLM flags not loaded at startup")
    return _vllm_flags
