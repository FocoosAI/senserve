"""Fetch and transform multimodal message parts."""

from __future__ import annotations

import base64
import glob
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes
from urllib.request import urlopen

from senserve.settings import get_settings


def parse_data_url(url: str) -> tuple[bytes, str]:
    if not url.startswith("data:"):
        raise ValueError("Invalid data URL; expected base64 encoding")
    header, _, payload = url.partition(",")
    if "base64" not in header:
        raise ValueError("Invalid data URL; expected base64 encoding")
    mime = "application/octet-stream"
    if ";" in header:
        mime = header[5:].split(";")[0] or mime
    try:
        return base64.b64decode(payload), mime
    except Exception as exc:
        raise ValueError("Invalid base64 in data URL") from exc


def fetch_url_bytes(url: str, max_bytes: int) -> tuple[bytes, str]:
    if url.startswith("data:"):
        data, mime = parse_data_url(url)
    else:
        with urlopen(url, timeout=60) as resp:  # noqa: S310 — user-provided URLs
            data = resp.read(max_bytes + 1)
            mime = resp.headers.get_content_type() or "application/octet-stream"
    if len(data) > max_bytes:
        raise ValueError(f"Attachment exceeds {max_bytes} bytes")
    return data, mime


def write_temp_file(data: bytes, suffix: str, temp_dir: Path) -> Path:
    temp_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=suffix, dir=temp_dir)
    os.close(fd)
    path = Path(name)
    path.write_bytes(data)
    return path


def extract_video_frames(video_path: Path, max_frames: int, temp_dir: Path) -> list[Path]:
    """Extract up to max_frames JPEG frames using ffmpeg."""
    pattern = str(temp_dir / f"{video_path.stem}_frame_%04d.jpg")
    vf = (
        f"select='not(mod(n\\,max(1\\,floor(n/{max_frames}))))',scale=512:-1"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            vf,
            "-frames:v",
            str(max_frames),
            pattern,
        ],
        check=True,
        capture_output=True,
    )
    return sorted(Path(p) for p in glob.glob(str(temp_dir / f"{video_path.stem}_frame_*.jpg")))


def part_to_image_url(path: Path) -> dict[str, Any]:
    data = base64.standard_encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{data}"},
    }


def expand_message_parts(
    parts: list[Any], max_frames: int, max_bytes: int, temp_dir: Path
) -> list[Any]:
    """Expand video_url parts into image_url frames; pass through other parts."""
    out: list[Any] = []
    for part in parts:
        if not isinstance(part, dict):
            out.append(part)
            continue
        ptype = part.get("type", "")
        if ptype not in ("video_url", "video"):
            out.append(part)
            continue
        block = part.get("video_url") or part.get("video") or {}
        url = block.get("url", "") if isinstance(block, dict) else ""
        data, _mime = fetch_url_bytes(url, max_bytes)
        suffix = ".mp4" if "mp4" in url.lower() else ".bin"
        video_path = write_temp_file(data, suffix, temp_dir)
        for frame in extract_video_frames(video_path, max_frames, temp_dir):
            out.append(part_to_image_url(frame))
    return out
