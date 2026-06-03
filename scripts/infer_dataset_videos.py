#!/usr/bin/env python3
"""Run chat completions on every video in a dataset folder via the Senserve gateway."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import httpx

PROMPT = "describe the video, search for any anomalies"
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"}
MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".m4v": "video/mp4",
}


def iter_videos(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Dataset directory not found: {directory}")
    paths = sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    )
    if not paths:
        raise FileNotFoundError(f"No video files in {directory}")
    return paths


def video_data_url(path: Path) -> str:
    mime = MIME_BY_SUFFIX.get(path.suffix.lower(), "video/mp4")
    payload = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def video_file_url(path: Path, vllm_media_root: Path) -> str:
    """Path as seen inside the Senserve/vLLM container (see compose datasets mount)."""
    return f"file://{(vllm_media_root / path.name).as_posix()}"


def resolve_model_id(client: httpx.Client, api_root: str, model: str) -> str:
    if model != "auto":
        return model
    r = client.get(f"{api_root}/health")
    r.raise_for_status()
    body = r.json()
    if not body.get("ready"):
        raise SystemExit(f"Gateway not ready (engine={body.get('engine')})")
    active = body.get("active_model")
    if not active:
        raise SystemExit("No model loaded; start senserve with a model or POST /v1/admin/models/load")
    return active


def assert_video_capable(client: httpx.Client, v1_base: str, model_id: str) -> None:
    r = client.get(f"{v1_base}/models")
    r.raise_for_status()
    for entry in r.json().get("data", []):
        if entry.get("id") != model_id:
            continue
        caps = entry.get("capabilities")
        if caps is not None and "video" not in caps:
            raise SystemExit(f"Model {model_id!r} has no video capability: {caps}")
        return
    raise SystemExit(f"Unknown model id: {model_id}")


def chat_completion(
    client: httpx.Client,
    v1_base: str,
    model_id: str,
    messages: list[dict],
    *,
    max_retries: int = 40,
) -> dict:
    url = f"{v1_base}/chat/completions"
    body = {"model": model_id, "messages": messages}
    for _ in range(max_retries):
        r = client.post(url, json=body, timeout=None)
        if r.status_code == 503:
            wait = int(r.headers.get("Retry-After", "30"))
            print(f"  model switching, retry in {wait}s …", file=sys.stderr)
            time.sleep(wait)
            continue
        if r.status_code >= 400:
            detail = r.text
            try:
                detail = r.json()
            except json.JSONDecodeError:
                pass
            raise httpx.HTTPStatusError(
                f"chat/completions failed: {detail}",
                request=r.request,
                response=r,
            )
        return r.json()
    raise RuntimeError("chat/completions still 503 after retries")


def assistant_text(response: dict) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected response shape: {response!r}") from exc
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=repo_root / "datasets",
        help="Folder with video files (default: ./datasets)",
    )
    parser.add_argument(
        "--api",
        default="http://127.0.0.1:8787",
        help="Senserve gateway root URL (default: http://127.0.0.1:8787)",
    )
    parser.add_argument(
        "--model",
        default="auto",
        help='Model id or "auto" for the currently loaded model',
    )
    parser.add_argument(
        "--prompt",
        default=PROMPT,
        help="User prompt sent with each video",
    )
    parser.add_argument(
        "--jsonl",
        type=Path,
        help="Optional path to write one JSON object per video",
    )
    parser.add_argument(
        "--media",
        choices=("file", "base64"),
        default="base64",
        help="base64: data URL in body (default); file: file:// URL under --vllm-media-root",
    )
    parser.add_argument(
        "--vllm-media-root",
        type=Path,
        default=Path("/datasets"),
        help="Container path where ./datasets is mounted (default: /datasets)",
    )
    args = parser.parse_args()

    api_root = args.api.rstrip("/")
    v1_base = f"{api_root}/v1"
    videos = iter_videos(args.dataset)

    with httpx.Client() as client:
        model_id = resolve_model_id(client, api_root, args.model)
        assert_video_capable(client, v1_base, model_id)
        media = args.media
        print(
            f"Model: {model_id}  |  videos: {len(videos)}  |  media: {media}",
            file=sys.stderr,
        )

        jsonl_file = args.jsonl.open("w", encoding="utf-8") if args.jsonl else None
        try:
            for path in videos:
                print(f"\n=== {path.name} ===\n", flush=True)
                if media == "file":
                    video_url = video_file_url(path, args.vllm_media_root)
                else:
                    video_url = video_data_url(path)
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": args.prompt},
                            {
                                "type": "video_url",
                                "video_url": {"url": video_url},
                            },
                        ],
                    }
                ]
                response = chat_completion(client, v1_base, model_id, messages)
                text = assistant_text(response)
                print(text)
                if jsonl_file:
                    record = {
                        "file": path.name,
                        "model": model_id,
                        "prompt": args.prompt,
                        "response": text,
                    }
                    jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    jsonl_file.flush()
        finally:
            if jsonl_file:
                jsonl_file.close()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (httpx.HTTPError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
