#!/usr/bin/env python3
"""Gate check: vLLM sleep-mode endpoints on the target image (run on DGX before deploy)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify vLLM --enable-sleep-mode support")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--model", default="Qwen/Qwen3.5-0.8B")
    parser.add_argument("--skip-serve", action="store_true", help="Only probe an already-running server")
    args = parser.parse_args()

    if not args.skip_serve:
        if not shutil.which("vllm"):
            print("FAIL: vllm not in PATH", file=sys.stderr)
            return 1
        help_out = subprocess.run(
            ["vllm", "serve", "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        combined = help_out.stdout + help_out.stderr
        if "enable-sleep-mode" not in combined and "enable_sleep_mode" not in combined:
            print("FAIL: vllm serve --help has no --enable-sleep-mode flag", file=sys.stderr)
            return 1
        print("OK: --enable-sleep-mode flag present")

    base = f"http://127.0.0.1:{args.port}"
    try:
        r = httpx.post(f"{base}/sleep", params={"level": 2}, timeout=10.0)
        if r.status_code == 404:
            print(
                "FAIL: POST /sleep returned 404 — enable VLLM_SERVER_DEV_MODE=1 and --enable-sleep-mode",
                file=sys.stderr,
            )
            return 1
        r.raise_for_status()
        sleeping = httpx.get(f"{base}/is_sleeping", timeout=10.0)
        sleeping.raise_for_status()
        print(f"OK: /sleep and /is_sleeping responded (is_sleeping={sleeping.text.strip()})")
    except httpx.HTTPError as exc:
        print(f"FAIL: HTTP probe: {exc}", file=sys.stderr)
        return 1

    print("PASS: vLLM sleep mode available on this host")
    return 0


if __name__ == "__main__":
    sys.exit(main())
