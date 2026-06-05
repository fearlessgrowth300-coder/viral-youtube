from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import requests

from .config import AgentConfig
from .ffmpeg import ffmpeg_bin


class SourcePermissionError(RuntimeError):
    pass


ProgressCallback = Callable[[dict[str, Any]], None]
YOUTUBE_HOST_RE = re.compile(r"(^|\.)youtube\.com$|(^|\.)youtu\.be$")


def emit_source_progress(
    progress: ProgressCallback | None,
    phase: str,
    percent: int,
    message: str,
) -> None:
    if progress:
        progress({"phase": phase, "progress": max(0, min(100, percent)), "message": message})


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "rtmp", "rtmps"}


def is_youtube_url(value: str) -> bool:
    if not is_url(value):
        return False
    parsed = urlparse(value)
    return bool(YOUTUBE_HOST_RE.search(parsed.hostname or ""))


def is_youtube_live_url(value: str) -> bool:
    if not is_youtube_url(value):
        return False
    return "live" in [part.lower() for part in urlparse(value).path.split("/") if part]


def extract_youtube_id(value: str) -> str:
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if host.endswith("youtu.be"):
        return parsed.path.strip("/").split("/", 1)[0]
    query_id = parse_qs(parsed.query).get("v", [""])[0]
    if query_id:
        return query_id
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"live", "shorts", "embed"}:
        return parts[1]
    return ""


def prepare_source(
    source: str,
    run_dir: Path,
    config: AgentConfig,
    progress: ProgressCallback | None = None,
    live_capture_seconds: int = 300,
) -> str:
    if is_url(source):
        if is_youtube_url(source):
            if not config.allow_youtube_download:
                raise SourcePermissionError(
                    "YouTube URL downloading is disabled. Set ALLOW_YOUTUBE_DOWNLOAD=true "
                    "only for content you own or have permission to process."
                )
            return download_youtube_source(
                source,
                run_dir,
                progress=progress,
                live_capture_seconds=live_capture_seconds,
            )
        emit_source_progress(progress, "download", 8, "Using remote media URL")
        return source

    candidate_path = Path(source)
    if candidate_path.exists():
        emit_source_progress(progress, "download", 8, "Using local source file")
        return str(candidate_path.resolve())

    raise FileNotFoundError(f"Source does not exist and is not a URL: {source}")


def download_youtube_source(
    source: str,
    run_dir: Path,
    progress: ProgressCallback | None = None,
    live_capture_seconds: int = 300,
) -> str:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError(
            "Install the youtube-download extra to use authorized YouTube URL downloads: "
            'python -m pip install -e ".[youtube-download]"'
        ) from exc

    downloads = run_dir / "source"
    downloads.mkdir(parents=True, exist_ok=True)
    emit_source_progress(progress, "analyze", 6, "Checking YouTube source")
    info_options = youtube_info_options()
    with yt_dlp.YoutubeDL(info_options) as downloader:
        info = downloader.extract_info(source, download=False)
    if should_capture_youtube_source(source, info):
        return record_youtube_live_source(info, downloads, progress, live_capture_seconds)

    options = youtube_download_options(downloads, progress=progress)
    emit_source_progress(progress, "download", 8, "Downloading YouTube source")
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(source, download=True)
        filename = downloader.prepare_filename(info)
    mp4_path = Path(filename).with_suffix(".mp4")
    if mp4_path.exists():
        resolved = str(mp4_path.resolve())
    else:
        resolved = str(Path(filename).resolve())
    emit_source_progress(progress, "download", 34, "Source download ready")
    return resolved


def youtube_info_options() -> dict:
    return {
        "format": "b[height<=1080]/best",
        "ffmpeg_location": ffmpeg_bin(),
        "no_color": True,
        "noplaylist": True,
        "quiet": True,
        "skip_download": True,
    }


def is_live_info(info: dict) -> bool:
    live_status = str(info.get("live_status") or "").lower()
    return bool(info.get("is_live")) or live_status == "is_live"


def should_capture_youtube_source(source: str, info: dict) -> bool:
    return is_live_info(info) or is_youtube_live_url(source)


def best_stream_url(info: dict) -> str:
    if info.get("url") and info.get("protocol") != "http_dash_segments":
        return str(info["url"])
    formats = info.get("formats") or []
    combined = [
        item
        for item in formats
        if item.get("url")
        and item.get("vcodec") != "none"
        and item.get("acodec") != "none"
        and (item.get("height") or 0) <= 1080
    ]
    if combined:
        return str(sorted(combined, key=lambda item: item.get("height") or 0, reverse=True)[0]["url"])
    hls = [
        item
        for item in formats
        if item.get("url")
        and "m3u8" in str(item.get("protocol") or item.get("url"))
        and item.get("vcodec") != "none"
    ]
    if hls:
        return str(sorted(hls, key=lambda item: item.get("height") or 0, reverse=True)[0]["url"])
    raise RuntimeError("Could not find a playable live stream URL.")


def record_youtube_live_source(
    info: dict,
    downloads: Path,
    progress: ProgressCallback | None,
    live_capture_seconds: int,
) -> str:
    seconds = max(30, min(1800, int(live_capture_seconds or 300)))
    title = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(info.get("title") or "youtube_live")).strip("_")
    output = downloads / f"{title[:70]}-live-capture.mp4"
    stream_url = best_stream_url(info)
    label = "Recording live segment" if is_live_info(info) else "Capturing YouTube live replay segment"
    emit_source_progress(progress, "live", 8, f"{label} ({seconds // 60} min)")
    command = [
        ffmpeg_bin(),
        "-y",
        *live_ffmpeg_input_args(info),
        "-t",
        str(seconds),
        "-i",
        stream_url,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output),
    ]
    returncode, log_tail = run_live_capture(command, progress, seconds, 8, 34)
    if returncode != 0 or not output.exists() or output.stat().st_size < 1024:
        emit_source_progress(progress, "live", 14, "Retrying live segment with transcoding")
        command = [
            ffmpeg_bin(),
            "-y",
            *live_ffmpeg_input_args(info),
            "-t",
            str(seconds),
            "-i",
            stream_url,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output),
        ]
        returncode, log_tail = run_live_capture(command, progress, seconds, 14, 34)
    if returncode != 0:
        raise RuntimeError(f"Live capture failed: {log_tail}")
    download_youtube_caption_sidecar(info, output, progress)
    emit_source_progress(progress, "live", 34, "Live segment ready")
    return str(output.resolve())


def download_youtube_caption_sidecar(
    info: dict,
    output: Path,
    progress: ProgressCallback | None,
) -> Path | None:
    caption = choose_caption(info)
    if not caption:
        return None
    url = str(caption.get("url") or "")
    if not url:
        return None
    headers = info.get("http_headers") or {}
    try:
        emit_source_progress(progress, "live", 31, "Downloading YouTube transcript")
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return None

    ext = str(caption.get("ext") or "").lower()
    text = response.text
    if ext == "json3" or text.lstrip().startswith("{"):
        text = json3_to_vtt(text)
        ext = "vtt"
    if ext not in {"vtt", "srt"}:
        ext = "vtt"
    if ext == "vtt" and not text.lstrip().startswith("WEBVTT"):
        text = "WEBVTT\n\n" + text
    language = str(caption.get("language") or "en")
    destination = output.with_name(f"{output.stem}.{language}.{ext}")
    destination.write_text(text, encoding="utf-8")
    return destination


def choose_caption(info: dict) -> dict[str, Any] | None:
    pools = [info.get("subtitles") or {}, info.get("automatic_captions") or {}]
    for pool in pools:
        for language in ("en", "en-US", "en-GB"):
            choices = pool.get(language) or []
            caption = best_caption_choice(choices, language)
            if caption:
                return caption
        for language, choices in pool.items():
            if str(language).startswith("en"):
                caption = best_caption_choice(choices, str(language))
                if caption:
                    return caption
    return None


def best_caption_choice(choices: list[dict[str, Any]], language: str) -> dict[str, Any] | None:
    if not choices:
        return None
    priority = {"vtt": 0, "srt": 1, "json3": 2}
    usable = [item for item in choices if item.get("url")]
    if not usable:
        return None
    selected = sorted(usable, key=lambda item: priority.get(str(item.get("ext") or "").lower(), 9))[0]
    return {**selected, "language": language}


def json3_to_vtt(text: str) -> str:
    import json

    payload = json.loads(text)
    cues: list[str] = ["WEBVTT", ""]
    for event in payload.get("events") or []:
        segments = event.get("segs") or []
        body = "".join(str(segment.get("utf8") or "") for segment in segments).strip()
        if not body:
            continue
        start = float(event.get("tStartMs") or 0) / 1000
        duration = float(event.get("dDurationMs") or 1800) / 1000
        end = start + max(0.2, duration)
        cues.extend([f"{format_vtt_time(start)} --> {format_vtt_time(end)}", body, ""])
    return "\n".join(cues)


def format_vtt_time(seconds: float) -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    whole = int(seconds)
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def live_ffmpeg_input_args(info: dict) -> list[str]:
    headers = info.get("http_headers") or {}
    header_text = "".join(f"{key}: {value}\r\n" for key, value in headers.items() if value)
    args: list[str] = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    if header_text:
        args.extend(["-headers", header_text])
    return args


def parse_ffmpeg_seconds(line: str) -> float | None:
    if line.startswith("out_time_ms="):
        try:
            return float(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    if line.startswith("out_time="):
        value = line.split("=", 1)[1].strip()
        parts = value.split(":")
        if len(parts) != 3:
            return None
        try:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            return None
    return None


def run_live_capture(
    command: list[str],
    progress: ProgressCallback | None,
    seconds: int,
    start_percent: int,
    end_percent: int,
) -> tuple[int, str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    log_tail: list[str] = []
    last_emit = 0.0
    timed_out = False

    def kill_stalled_capture() -> None:
        nonlocal timed_out
        timed_out = True
        process.kill()

    watchdog = threading.Timer(max(90, seconds + 120), kill_stalled_capture)
    watchdog.start()
    try:
        for line in process.stdout:
            log_tail.append(line.rstrip())
            log_tail = log_tail[-40:]
            elapsed = parse_ffmpeg_seconds(line)
            now = time.time()
            if elapsed is not None and now - last_emit > 1.5:
                ratio = max(0.0, min(1.0, elapsed / max(1, seconds)))
                percent = start_percent + int((end_percent - start_percent) * ratio)
                emit_source_progress(
                    progress,
                    "live",
                    percent,
                    f"Recording live segment {int(ratio * 100)}%",
                )
                last_emit = now
    except Exception:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        watchdog.cancel()
    returncode = process.wait()
    if timed_out:
        log_tail.append("Live capture timed out before ffmpeg finished.")
    return returncode, "\n".join(log_tail)[-1200:]


def youtube_download_options(
    downloads: Path,
    progress: ProgressCallback | None = None,
) -> dict:
    output_template = str(downloads / "%(title).80s-%(id)s.%(ext)s")
    hooks = [youtube_progress_hook(progress)] if progress else []
    return {
        "outtmpl": output_template,
        "format": "bv*[height<=1080]+ba/b[height<=1080]/best[height<=1080]/best",
        "ffmpeg_location": ffmpeg_bin(),
        "merge_output_format": "mp4",
        "no_color": True,
        "noplaylist": True,
        "progress_hooks": hooks,
        "restrictfilenames": True,
        "quiet": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "file_access_retries": 3,
        "subtitlesformat": "vtt/srt/best",
        "subtitleslangs": ["en", "en.*"],
        "writeautomaticsub": True,
        "writesubtitles": True,
    }


def analyze_source(source: str) -> dict:
    if not is_url(source):
        path = Path(source)
        return {
            "source": source,
            "title": path.name if path.exists() else source,
            "is_youtube": False,
            "thumbnail": "",
            "duration": None,
            "duration_string": "",
            "has_captions": False,
        }
    if not is_youtube_url(source):
        return {
            "source": source,
            "title": source,
            "is_youtube": False,
            "thumbnail": "",
            "duration": None,
            "duration_string": "",
            "has_captions": False,
        }
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError(
            "Install the youtube-download extra to analyze YouTube links: "
            'python -m pip install -e ".[youtube-download]"'
        ) from exc

    options = youtube_info_options()
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(source, download=False)
    except Exception as exc:
        video_id = extract_youtube_id(source)
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
        return {
            "source": source,
            "title": f"YouTube video {video_id}" if video_id else source,
            "is_youtube": True,
            "thumbnail": thumbnail,
            "duration": None,
            "duration_string": "",
            "is_live": "/live/" in urlparse(source).path,
            "live_status": "unknown",
            "webpage_url": source,
            "has_captions": False,
            "caption_languages": [],
            "analysis_error": clean_youtube_error(str(exc)),
        }
    subtitles = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    return {
        "source": source,
        "title": info.get("title") or source,
        "is_youtube": True,
        "thumbnail": info.get("thumbnail") or "",
        "duration": info.get("duration"),
        "duration_string": info.get("duration_string") or "",
        "is_live": is_live_info(info) or is_youtube_live_url(source),
        "live_status": info.get("live_status") or "",
        "webpage_url": info.get("webpage_url") or source,
        "has_captions": bool(subtitles or automatic),
        "caption_languages": sorted(set(subtitles.keys()) | set(automatic.keys()))[:12],
    }


def clean_youtube_error(error: str) -> str:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", error)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:260] or "YouTube analysis failed."


def is_blocking_youtube_error(error: str) -> bool:
    lower = clean_youtube_error(error).lower()
    blocking_phrases = (
        "not available",
        "video unavailable",
        "private video",
        "removed",
        "has been terminated",
        "members-only",
        "sign in to confirm",
        "copyright",
    )
    return any(phrase in lower for phrase in blocking_phrases)


def youtube_progress_hook(progress: ProgressCallback, stall_seconds: int = 90):
    last_progress = {"time": time.monotonic(), "bytes": 0.0}

    def hook(event: dict) -> None:
        status = event.get("status")
        if status == "downloading":
            downloaded = float(event.get("downloaded_bytes") or 0)
            now = time.monotonic()
            if downloaded > last_progress["bytes"] + 1024:
                last_progress["time"] = now
                last_progress["bytes"] = downloaded
            elif now - last_progress["time"] > stall_seconds:
                raise RuntimeError(
                    "YouTube download stalled. Try a shorter live capture time, another link, or upload a local file."
                )
            total = float(event.get("total_bytes") or event.get("total_bytes_estimate") or 0)
            if total > 0:
                percent = 8 + int((downloaded / total) * 24)
                message = f"Downloading source {downloaded / total:.0%}"
            else:
                percent = 12
                message = "Downloading source"
            emit_source_progress(progress, "download", percent, message)
        elif status == "finished":
            emit_source_progress(progress, "download", 32, "Merging downloaded media")

    return hook
