from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path


class FFmpegNotFound(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    env = os.getenv("FFMPEG_BIN")
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - depends on local install
        raise FFmpegNotFound(
            "ffmpeg was not found. Install this project dependency or set FFMPEG_BIN."
        ) from exc


def ffprobe_bin() -> str | None:
    env = os.getenv("FFPROBE_BIN")
    if env:
        return env
    return shutil.which("ffprobe")


def run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def probe_duration(source: str) -> float:
    probe = ffprobe_bin()
    if probe:
        result = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                source,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout or "{}")
            duration = data.get("format", {}).get("duration")
            if duration:
                return float(duration)

    result = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-i", source],
        check=False,
        capture_output=True,
        text=True,
    )
    text = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def escape_filter_path(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("\\", "/")
    escaped = escaped.replace(":", "\\:").replace("'", "\\'")
    return escaped
