from __future__ import annotations

import argparse
import base64
import hmac
import json
import mimetypes
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .ai_select import analyze_transcript_for_viral_moments
from .config import AgentConfig, get_or_create_settings_pin, save_local_settings
from .pipeline import RunOptions, run_once
from .paths import CACHE_ROOT, DATA_ROOT, PROJECT_ROOT, RUNS_ROOT, ensure_data_directories
from .publishers.dispatcher import publish_queue
from .source import analyze_source, is_blocking_youtube_error, is_youtube_url
from .transcribe import (
    TranscriptAPICreditsExhausted,
    TranscriptAPIError,
    fetch_transcript_api,
    youtube_cache_key,
)


ASSETS_DIR = Path(__file__).resolve().parent / "web_assets"
ALLOWED_PLATFORMS = {"youtube", "tiktok", "instagram"}


@dataclass
class JobState:
    id: str
    source: str
    status: str = "queued"
    message: str = "Queued"
    phase: str = "queued"
    progress: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_requested: bool = False

    def to_jsonable(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at_label"] = format_timestamp(self.created_at)
        payload["started_at_label"] = format_timestamp(self.started_at)
        payload["finished_at_label"] = format_timestamp(self.finished_at)
        return payload


class WebState:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.settings_pin = get_or_create_settings_pin()
        self.site_username = os.getenv("APP_USERNAME", "admin").strip() or "admin"
        self.site_password = os.getenv("APP_PASSWORD", "")
        self.jobs: dict[str, JobState] = {}
        self.cancelled_job_ids: set[str] = set()
        self.lock = threading.Lock()

    def put_job(self, job: JobState) -> None:
        with self.lock:
            self.jobs[job.id] = job

    def update_job(self, job_id: str, **updates: Any) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            for key, value in updates.items():
                setattr(job, key, value)
            return True

    def request_cancel(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            self.cancelled_job_ids.add(job_id)
            job = self.jobs.get(job_id)
            if not job:
                return None
            job.cancel_requested = True
            if job.status in {"queued", "running"}:
                job.status = "cancelling"
                job.message = "Stopping job"
                job.phase = "cancelling"
            return job.to_jsonable()

    def delete_job(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            self.cancelled_job_ids.add(job_id)
            job = self.jobs.pop(job_id, None)
            return {"deleted": bool(job), "job_id": job_id}

    def should_cancel(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            return job_id in self.cancelled_job_ids or bool(job and job.cancel_requested)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                job.to_jsonable()
                for job in sorted(self.jobs.values(), key=lambda item: item.created_at, reverse=True)
            ]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return job.to_jsonable() if job else None


class JobCancelled(RuntimeError):
    pass


def format_timestamp(value: float | None) -> str:
    if not value:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def inside_project(path: Path) -> bool:
    return any(inside_directory(path, root) for root in (PROJECT_ROOT, DATA_ROOT))


def inside_directory(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def resolve_project_path(raw_path: str) -> Path:
    candidate = Path(unquote(raw_path))
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    if not inside_project(resolved):
        raise PermissionError(f"Path is outside the project: {raw_path}")
    return resolved


def resolve_runs_path(raw_path: str) -> Path:
    resolved = resolve_project_path(raw_path)
    if not inside_directory(resolved, RUNS_ROOT):
        raise PermissionError(f"Path is outside generated runs: {raw_path}")
    return resolved


def analyze_source_content(source: str, config: AgentConfig) -> dict[str, Any]:
    analysis = analyze_source(source)
    if not is_youtube_url(source):
        return analysis

    analysis_error = str(analysis.get("analysis_error") or "")
    if analysis_error and is_blocking_youtube_error(analysis_error):
        analysis["transcript_status"] = "unavailable"
        analysis["viral_analysis"] = {"provider": "none", "moments": []}
        return analysis
    if not config.transcript_api_key:
        analysis["transcript_status"] = "not_configured"
        analysis["viral_analysis"] = {"provider": "none", "moments": []}
        return analysis

    analysis_dir = CACHE_ROOT / "source-analysis" / youtube_cache_key(source)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    try:
        transcript = fetch_transcript_api(source, analysis_dir, config)
    except TranscriptAPICreditsExhausted as exc:
        analysis["transcript_status"] = "credits_exhausted"
        analysis["credential_alert"] = str(exc)
        analysis["viral_analysis"] = {"provider": "none", "moments": []}
        return analysis
    except TranscriptAPIError as exc:
        analysis["transcript_status"] = "error"
        analysis["transcript_error"] = str(exc)
        analysis["viral_analysis"] = {"provider": "none", "moments": []}
        return analysis
    except Exception as exc:
        analysis["transcript_status"] = "error"
        analysis["transcript_error"] = f"Transcript analysis failed: {exc}"
        analysis["viral_analysis"] = {"provider": "none", "moments": []}
        return analysis

    if not transcript:
        analysis["transcript_status"] = "unavailable"
        analysis["transcript_segments"] = 0
        analysis["viral_analysis"] = {"provider": "none", "moments": []}
        return analysis

    duration = float(analysis.get("duration") or max(segment.end for segment in transcript))
    viral_analysis = analyze_transcript_for_viral_moments(
        source,
        transcript,
        duration,
        config,
    )
    analysis["duration"] = analysis.get("duration") or duration
    analysis["has_captions"] = True
    analysis["transcript_status"] = "ready"
    analysis["transcript_segments"] = len(transcript)
    analysis["transcript_preview"] = " ".join(
        segment.text for segment in transcript[:6]
    )[:600]
    analysis["viral_analysis"] = viral_analysis
    return analysis


def find_run_dir(path: Path) -> Path:
    current = path if path.is_dir() else path.parent
    runs_root = RUNS_ROOT.resolve()
    while inside_directory(current, runs_root):
        if (current / "manifest.json").exists():
            return current
        if current == runs_root:
            break
        current = current.parent
    raise FileNotFoundError("Could not find a manifest for this clip.")


def media_url(path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    return f"/media?path={quote(candidate.as_posix(), safe='')}"


def basic_auth_matches(
    authorization: str,
    expected_username: str,
    expected_password: str,
) -> bool:
    if not expected_password:
        return True
    scheme, _, encoded = authorization.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    username, separator, password = decoded.partition(":")
    return bool(
        separator
        and hmac.compare_digest(username, expected_username)
        and hmac.compare_digest(password, expected_password)
    )


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def write_json(handler: BaseHTTPRequestHandler, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def content_type_for_path(path: Path) -> str:
    if path.name == "manifest.webmanifest":
        return "application/manifest+json"
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def write_file(handler: BaseHTTPRequestHandler, path: Path, send_body: bool = True) -> None:
    if not path.exists() or not path.is_file():
        handler.send_error(HTTPStatus.NOT_FOUND)
        return
    content_type = content_type_for_path(path)
    file_size = path.stat().st_size
    range_header = handler.headers.get("Range", "")
    if range_header.startswith("bytes="):
        start_text, _, end_text = range_header.removeprefix("bytes=").partition("-")
        try:
            if start_text:
                start = int(start_text)
                end = int(end_text) if end_text else file_size - 1
            else:
                suffix_length = int(end_text)
                start = max(0, file_size - suffix_length)
                end = file_size - 1
        except ValueError:
            handler.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return
        start = max(0, min(start, file_size - 1))
        end = max(start, min(end, file_size - 1))
        length = end - start + 1
        data = b""
        if send_body:
            with path.open("rb") as file:
                file.seek(start)
                data = file.read(length)
        handler.send_response(HTTPStatus.PARTIAL_CONTENT)
        handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        handler.send_header("Content-Length", str(length))
    else:
        data = path.read_bytes() if send_body else b""
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Length", str(file_size))
    handler.send_header("Content-Type", content_type)
    if path.suffix.lower() in {".mp4", ".mov", ".webm"}:
        handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    if send_body:
        handler.wfile.write(data)


def safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def sanitize_platforms(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(platform for platform in values if platform in ALLOWED_PLATFORMS)


def module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def publish_setup(config: AgentConfig) -> dict[str, Any]:
    youtube_secrets = config.youtube_client_secrets
    youtube_ready = bool(youtube_secrets and youtube_secrets.exists())
    google_libs_ready = all(
        module_available(name)
        for name in ("googleapiclient.discovery", "google_auth_oauthlib.flow", "google.oauth2.credentials")
    )
    return {
        "enabled": config.publish_enabled,
        "manual_review": config.require_manual_review,
        "youtube": {
            "ready": config.publish_enabled and youtube_ready and google_libs_ready,
            "configured": youtube_ready,
            "dependency_ready": google_libs_ready,
            "message": "Ready" if config.publish_enabled and youtube_ready and google_libs_ready else youtube_setup_message(config, youtube_ready, google_libs_ready),
        },
        "tiktok": {
            "ready": config.publish_enabled and bool(config.tiktok_access_token),
            "configured": bool(config.tiktok_access_token),
            "message": "Ready" if config.publish_enabled and config.tiktok_access_token else tiktok_setup_message(config),
        },
        "instagram": {
            "ready": config.publish_enabled
            and bool(config.instagram_access_token)
            and bool(config.instagram_user_id)
            and bool(config.public_media_base_url),
            "configured": bool(config.instagram_access_token and config.instagram_user_id and config.public_media_base_url),
            "message": "Ready" if config.publish_enabled and config.instagram_access_token and config.instagram_user_id and config.public_media_base_url else instagram_setup_message(config),
        },
    }


def settings_status(config: AgentConfig) -> dict[str, Any]:
    return {
        "transcript_api_configured": bool(config.transcript_api_key),
        "openai_configured": bool(config.openai_api_key),
        "youtube_client_secrets_configured": bool(config.youtube_client_secrets),
        "youtube_client_secrets": str(config.youtube_client_secrets or ""),
        "tiktok_configured": bool(config.tiktok_access_token),
        "instagram_configured": bool(config.instagram_access_token),
        "instagram_user_id": config.instagram_user_id or "",
        "public_media_base_url": config.public_media_base_url or "",
        "publish_enabled": config.publish_enabled,
    }


def update_app_settings(payload: dict[str, Any], state: WebState, supplied_pin: str) -> dict[str, Any]:
    if not supplied_pin or supplied_pin != state.settings_pin:
        raise PermissionError("The Settings PIN is incorrect.")
    updates: dict[str, object] = {}
    field_map = {
        "transcript_api_key": "TRANSCRIPT_API_KEY",
        "openai_api_key": "OPENAI_API_KEY",
        "youtube_client_secrets": "YOUTUBE_CLIENT_SECRETS",
        "tiktok_access_token": "TIKTOK_ACCESS_TOKEN",
        "instagram_access_token": "INSTAGRAM_ACCESS_TOKEN",
        "instagram_user_id": "INSTAGRAM_USER_ID",
        "public_media_base_url": "PUBLIC_MEDIA_BASE_URL",
    }
    for field_name, setting_name in field_map.items():
        value = str(payload.get(field_name) or "").strip()
        if value:
            updates[setting_name] = value
    if "publish_enabled" in payload:
        updates["PUBLISH_ENABLED"] = "true" if safe_bool(payload.get("publish_enabled")) else "false"
    save_local_settings(updates)
    state.config = AgentConfig.from_env()
    return {
        "saved": True,
        "settings": settings_status(state.config),
        "message": "Settings saved. New jobs will use the updated credentials.",
    }


def youtube_setup_message(config: AgentConfig, has_secrets: bool, has_deps: bool) -> str:
    if not config.publish_enabled:
        return "Set PUBLISH_ENABLED=true in .env and restart."
    if not has_secrets:
        return "Set YOUTUBE_CLIENT_SECRETS to a Google OAuth client JSON file."
    if not has_deps:
        return 'Install publish dependencies: python -m pip install -e ".[publish]"'
    return "Ready"


def tiktok_setup_message(config: AgentConfig) -> str:
    if not config.publish_enabled:
        return "Set PUBLISH_ENABLED=true in .env and restart."
    if not config.tiktok_access_token:
        return "Set TIKTOK_ACCESS_TOKEN from a TikTok developer app with posting access."
    return "Ready"


def instagram_setup_message(config: AgentConfig) -> str:
    if not config.publish_enabled:
        return "Set PUBLISH_ENABLED=true in .env and restart."
    missing = []
    if not config.instagram_access_token:
        missing.append("INSTAGRAM_ACCESS_TOKEN")
    if not config.instagram_user_id:
        missing.append("INSTAGRAM_USER_ID")
    if not config.public_media_base_url:
        missing.append("PUBLIC_MEDIA_BASE_URL")
    return f"Set {', '.join(missing)}." if missing else "Ready"


def delete_run(raw_run_dir: str) -> dict[str, Any]:
    run_dir = resolve_runs_path(raw_run_dir)
    manifest_path = run_dir / "manifest.json"
    if not run_dir.is_dir() or not manifest_path.exists():
        raise FileNotFoundError("Run folder was not found.")
    shutil.rmtree(run_dir)
    return {"deleted": "run", "run_dir": str(run_dir)}


def same_resolved_path(raw_path: Any, target: Path) -> bool:
    if not raw_path:
        return False
    try:
        return resolve_runs_path(str(raw_path)) == target
    except (OSError, PermissionError, RuntimeError):
        return False


def safe_unlink(path: Path, run_dir: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if not inside_directory(resolved, run_dir) or not resolved.exists() or not resolved.is_file():
        return False
    resolved.unlink()
    return True


def prune_publish_queue(queue_path: Path, deleted_video_path: Path) -> int:
    if not queue_path.exists():
        return 0
    kept: list[str] = []
    removed = 0
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if same_resolved_path(entry.get("video_path"), deleted_video_path):
            removed += 1
            continue
        kept.append(json.dumps(entry, ensure_ascii=True))
    if kept:
        queue_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    else:
        queue_path.unlink()
    return removed


def delete_clip(raw_video_path: str, raw_run_dir: str | None = None) -> dict[str, Any]:
    video_path = resolve_runs_path(raw_video_path)
    run_dir = resolve_runs_path(raw_run_dir) if raw_run_dir else find_run_dir(video_path)
    if not run_dir.is_dir() or not (run_dir / "manifest.json").exists():
        raise FileNotFoundError("Run folder was not found.")
    if not inside_directory(video_path, run_dir):
        raise PermissionError("Clip is outside the selected run.")

    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    remaining_clips: list[dict[str, Any]] = []
    removed_clips: list[dict[str, Any]] = []
    for clip in manifest.get("clips", []):
        if same_resolved_path(clip.get("video_path"), video_path):
            removed_clips.append(clip)
        else:
            remaining_clips.append(clip)
    if not removed_clips and not video_path.exists():
        raise FileNotFoundError("Clip was not found.")

    delete_candidates = {
        video_path,
        video_path.with_suffix(".srt"),
        video_path.with_suffix(".json"),
        video_path.with_name(f"{video_path.stem}-thumbnail.jpg"),
        video_path.with_name(f"{video_path.stem}-voiceover.mp3"),
        video_path.with_name(f"{video_path.stem}-voiceover.wav"),
    }
    for clip in removed_clips:
        for key in ("video_path", "srt_path", "metadata_path", "thumbnail_path"):
            value = clip.get(key)
            if value:
                delete_candidates.add(resolve_runs_path(str(value)))

    deleted_files = 0
    for candidate in delete_candidates:
        if safe_unlink(candidate, run_dir):
            deleted_files += 1

    manifest["clips"] = remaining_clips
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    queue_removed = prune_publish_queue(run_dir / "publish_queue.jsonl", video_path)
    return {
        "deleted": "clip",
        "video_path": str(video_path),
        "deleted_files": deleted_files,
        "queue_entries_removed": queue_removed,
    }


def delete_generated(payload: dict[str, Any]) -> dict[str, Any]:
    delete_type = str(payload.get("type") or "").strip().lower()
    if delete_type == "run":
        return delete_run(str(payload.get("run_dir") or ""))
    if delete_type == "clip":
        return delete_clip(
            str(payload.get("video_path") or ""),
            str(payload.get("run_dir") or "") or None,
        )
    raise ValueError("Delete type must be 'run' or 'clip'.")


def latest_clip_preview(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for run in runs:
        clips = run.get("clips") or []
        if clips:
            return clips[0]
    sample = PROJECT_ROOT / "samples" / "sample.mp4"
    if sample.exists():
        return {
            "title": "Sample source",
            "video_path": str(sample),
            "video_url": media_url(sample),
        }
    return None


def list_runs() -> list[dict[str, Any]]:
    runs_dir = RUNS_ROOT
    if not runs_dir.exists():
        return []
    manifests = sorted(
        runs_dir.rglob("manifest.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    runs: list[dict[str, Any]] = []
    for manifest_path in manifests[:50]:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        run_dir = manifest_path.parent.resolve()
        clips = []
        for clip in manifest.get("clips", []):
            video_path = clip.get("video_path", "")
            srt_path = clip.get("srt_path", "")
            metadata_path = clip.get("metadata_path", "")
            thumbnail_path = clip.get("thumbnail_path", "")
            candidate = clip.get("candidate", {})
            clips.append(
                {
                    **clip,
                    "title": candidate.get("title") or Path(video_path).stem,
                    "video_url": media_url(video_path),
                    "srt_url": media_url(srt_path) if srt_path else "",
                    "metadata_url": media_url(metadata_path) if metadata_path else "",
                    "thumbnail_url": media_url(thumbnail_path) if thumbnail_path else "",
                }
            )
        queue_path = run_dir / "publish_queue.jsonl"
        has_queue = bool(clips and queue_path.exists() and queue_path.stat().st_size > 0)
        runs.append(
            {
                "id": str(run_dir.relative_to(DATA_ROOT)),
                "run_dir": str(run_dir),
                "run_dir_label": str(run_dir.relative_to(DATA_ROOT)),
                "source": manifest.get("source", ""),
                "duration": manifest.get("duration", 0),
                "transcript_segments": manifest.get("transcript_segments", 0),
                "created_at": format_timestamp(manifest_path.stat().st_mtime),
                "clip_count": len(clips),
                "clips": clips,
                "skipped": manifest.get("skipped", []),
                "queue_path": str(queue_path) if has_queue else "",
                "queue_url": media_url(queue_path) if has_queue else "",
            }
        )
    return runs


def start_run_job(payload: dict[str, Any], state: WebState) -> dict[str, Any]:
    use_sample = safe_bool(payload.get("use_sample"))
    source = str(payload.get("source") or "").strip()
    transcript = str(payload.get("transcript") or "").strip()
    if use_sample:
        source = str((PROJECT_ROOT / "samples" / "sample.mp4").resolve())
        transcript_path = PROJECT_ROOT / "samples" / "sample.srt"
    else:
        transcript_path = resolve_project_path(transcript) if transcript else None
    if not source:
        raise ValueError("Source is required.")
    youtube_authorized = safe_bool(payload.get("allow_youtube_download"))
    if is_youtube_url(source) and not (youtube_authorized or state.config.allow_youtube_download):
        raise ValueError(
            "Tick 'I own/have permission' before clipping a YouTube URL."
        )
    if is_youtube_url(source) and youtube_authorized:
        analysis = analyze_source(source)
        analysis_error = str(analysis.get("analysis_error") or "")
        if analysis_error and is_blocking_youtube_error(analysis_error):
            raise ValueError(
                "YouTube says this video/live is unavailable, so it cannot be clipped. "
                "Open the link in your browser first; if it does not play, use another public/active link "
                "or upload a local video file."
            )

    generation_mode = str(payload.get("generation_mode") or "short").strip().lower()
    if generation_mode not in {"short", "long"}:
        generation_mode = "short"
    if generation_mode == "long":
        requested_seconds = safe_int(payload.get("long_duration_seconds"), 600, 60, 7200)
        max_clips = 1
        vertical = False
    else:
        requested_seconds = safe_int(payload.get("clip_length"), 45, 5, 180)
        max_clips = safe_int(payload.get("clips"), 10, 1, 10)
        vertical = not safe_bool(payload.get("horizontal"))
    caption_style = str(payload.get("caption_style") or "bold").strip().lower()
    if caption_style == "none":
        caption_style = "bold"

    job = JobState(id=uuid.uuid4().hex[:12], source=source)
    state.put_job(job)
    options = RunOptions(
        source=source,
        out_dir=RUNS_ROOT / "web",
        transcript_path=transcript_path,
        max_clips=max_clips,
        clip_length_seconds=requested_seconds,
        vertical=vertical,
        enable_voiceover=True
        if payload.get("voiceover") is None
        else safe_bool(payload.get("voiceover")),
        auto_hook=True if payload.get("auto_hook") is None else safe_bool(payload.get("auto_hook")),
        clip_model=str(payload.get("clip_model") or "viral"),
        genre=str(payload.get("genre") or "auto"),
        caption_style=caption_style,
        render_quality=str(payload.get("render_quality") or "2k").lower(),
        live_capture_seconds=max(
            safe_int(payload.get("live_capture_minutes"), 5, 1, 120) * 60,
            requested_seconds if generation_mode == "long" else 0,
        ),
        platforms=sanitize_platforms(payload.get("platforms")),
        max_transcribe_seconds=safe_int(payload.get("max_transcribe_seconds"), 0, 0, 7200) or None,
        generation_mode=generation_mode,
        interaction_prompt=True
        if payload.get("interaction_prompt") is None
        else safe_bool(payload.get("interaction_prompt")),
        enable_broll=True
        if payload.get("enable_broll") is None
        else safe_bool(payload.get("enable_broll")),
        polish=True
        if payload.get("polish") is None
        else safe_bool(payload.get("polish")),
        seo_optimize=True
        if payload.get("seo_optimize") is None
        else safe_bool(payload.get("seo_optimize")),
        voice_provider=str(payload.get("voice_provider") or "local-piper"),
        narration_style=str(payload.get("narration_style") or "movie-recap"),
    )

    run_config = state.config
    if youtube_authorized:
        run_config = replace(run_config, allow_youtube_download=True)

    thread = threading.Thread(
        target=run_job_thread,
        args=(job.id, options, state, run_config),
        daemon=True,
    )
    thread.start()
    return job.to_jsonable()


def run_job_thread(
    job_id: str,
    options: RunOptions,
    state: WebState,
    config: AgentConfig,
) -> None:
    def on_progress(update: dict[str, Any]) -> None:
        if state.should_cancel(job_id):
            raise JobCancelled("Job cancelled by user")
        state.update_job(
            job_id,
            status="running",
            message=str(update.get("message") or "Working"),
            phase=str(update.get("phase") or "running"),
            progress=int(update.get("progress") or 0),
        )

    try:
        if state.should_cancel(job_id):
            raise JobCancelled("Job cancelled by user")
        state.update_job(
            job_id,
            status="running",
            message="Starting",
            phase="setup",
            progress=1,
            started_at=time.time(),
        )
        result = run_once(options, config, progress=on_progress)
        if state.should_cancel(job_id):
            raise JobCancelled("Job cancelled by user")
        result_payload = result.to_jsonable()
        if not result.clips and result.skipped:
            state.update_job(
                job_id,
                status="failed",
                message="No clips rendered",
                phase="failed",
                progress=100,
                result=result_payload,
                error="; ".join(result.skipped[:3]),
                finished_at=time.time(),
            )
        else:
            state.update_job(
                job_id,
                status="succeeded",
                message=f"Rendered {len(result.clips)} clip(s)",
                phase="done",
                progress=100,
                result=result_payload,
                finished_at=time.time(),
            )
    except JobCancelled as exc:
        state.update_job(
            job_id,
            status="cancelled",
            message="Run stopped",
            phase="cancelled",
            error=str(exc),
            finished_at=time.time(),
            cancel_requested=True,
        )
    except Exception as exc:
        state.update_job(
            job_id,
            status="failed",
            message="Run failed",
            phase="failed",
            error=str(exc),
            finished_at=time.time(),
        )


class ClipperRequestHandler(BaseHTTPRequestHandler):
    server_version = "ClipperWeb/0.1"

    @property
    def app_state(self) -> WebState:
        return self.server.app_state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        self.handle_read(send_body=True)

    def do_HEAD(self) -> None:
        self.handle_read(send_body=False)

    def require_site_auth(self) -> bool:
        if basic_auth_matches(
            self.headers.get("Authorization", ""),
            self.app_state.site_username,
            self.app_state.site_password,
        ):
            return False
        body = b'{"error":"Authentication required"}'
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Viral Video Clipper"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return True

    def handle_read(self, send_body: bool) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/healthz":
            write_json(self, {"status": "ok"})
            return
        if self.require_site_auth():
            return
        try:
            if path == "/":
                write_file(self, ASSETS_DIR / "index.html", send_body=send_body)
            elif path == "/manifest.webmanifest":
                write_file(self, ASSETS_DIR / "manifest.webmanifest", send_body=send_body)
            elif path == "/service-worker.js":
                write_file(self, ASSETS_DIR / "service-worker.js", send_body=send_body)
            elif path == "/favicon.svg":
                write_file(self, ASSETS_DIR / "icon.svg", send_body=send_body)
            elif path.startswith("/assets/"):
                target = (ASSETS_DIR / path.removeprefix("/assets/")).resolve()
                if not inside_directory(target, ASSETS_DIR):
                    raise PermissionError("Asset path is outside the web asset folder.")
                write_file(self, target, send_body=send_body)
            elif path == "/media":
                params = parse_qs(parsed.query)
                target = resolve_project_path(params.get("path", [""])[0])
                write_file(self, target, send_body=send_body)
            elif path == "/api/status":
                runs = list_runs()
                piper_model = self.app_state.config.piper_model_path
                if piper_model and not piper_model.is_absolute():
                    piper_model = PROJECT_ROOT / piper_model
                write_json(
                    self,
                    {
                        "project_root": str(PROJECT_ROOT),
                        "openai_configured": bool(self.app_state.config.openai_api_key),
                        "local_voice_ready": bool(
                            piper_model
                            and piper_model.exists()
                            and piper_model.with_suffix(piper_model.suffix + ".json").exists()
                        ),
                        "transcript_api_configured": bool(self.app_state.config.transcript_api_key),
                        "youtube_download_allowed": self.app_state.config.allow_youtube_download,
                        "publish_enabled": self.app_state.config.publish_enabled,
                        "publish_setup": publish_setup(self.app_state.config),
                        "require_manual_review": self.app_state.config.require_manual_review,
                        "sample_exists": (PROJECT_ROOT / "samples" / "sample.mp4").exists(),
                        "latest_clip": latest_clip_preview(runs),
                    },
                )
            elif path == "/api/jobs":
                write_json(self, {"jobs": self.app_state.list_jobs()})
            elif path == "/api/settings":
                write_json(self, {"settings": settings_status(self.app_state.config)})
            elif path.startswith("/api/jobs/"):
                job = self.app_state.get_job(path.rsplit("/", 1)[-1])
                write_json(self, job or {"error": "Job not found"}, HTTPStatus.OK if job else HTTPStatus.NOT_FOUND)
            elif path == "/api/runs":
                write_json(self, {"runs": list_runs()})
            elif path == "/api/analyze":
                params = parse_qs(parsed.query)
                source = params.get("source", [""])[0].strip()
                if not source:
                    write_json(self, {"error": "Source is required."}, HTTPStatus.BAD_REQUEST)
                else:
                    write_json(
                        self,
                        {"analysis": analyze_source_content(source, self.app_state.config)},
                    )
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            write_json(self, {"error": str(exc)}, HTTPStatus.FORBIDDEN)
        except Exception as exc:
            write_json(self, {"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if self.require_site_auth():
            return
        try:
            payload = read_json_body(self)
            if parsed.path == "/api/run":
                write_json(self, {"job": start_run_job(payload, self.app_state)}, HTTPStatus.ACCEPTED)
            elif parsed.path == "/api/settings":
                write_json(
                    self,
                    update_app_settings(
                        payload,
                        self.app_state,
                        self.headers.get("X-Settings-Pin", ""),
                    ),
                )
            elif parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                parts = [part for part in parsed.path.split("/") if part]
                job_id = parts[2] if len(parts) >= 4 else ""
                job = self.app_state.request_cancel(job_id)
                write_json(self, job or {"cancelled": True, "job_id": job_id})
            elif parsed.path == "/api/publish":
                queue = resolve_project_path(str(payload.get("queue_path") or ""))
                results = publish_queue(queue, self.app_state.config, approved=safe_bool(payload.get("approve")))
                write_json(
                    self,
                    {
                        "results": [asdict(result) for result in results],
                        "publish_setup": publish_setup(self.app_state.config),
                    },
                )
            elif parsed.path == "/api/delete":
                write_json(self, delete_generated(payload))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            write_json(self, {"error": str(exc)}, HTTPStatus.FORBIDDEN)
        except Exception as exc:
            write_json(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if self.require_site_auth():
            return
        try:
            if parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                write_json(self, self.app_state.delete_job(job_id))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            write_json(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)


class ClipperHTTPServer(ThreadingHTTPServer):
    app_state: WebState


def run_web_server(args: argparse.Namespace | None = None) -> None:
    ensure_data_directories()
    host = getattr(args, "host", os.getenv("HOST", "127.0.0.1"))
    port = int(getattr(args, "port", int(os.getenv("PORT", "8010"))))
    config = AgentConfig.from_env()
    server = ClipperHTTPServer((host, port), ClipperRequestHandler)
    server.app_state = WebState(config)
    print(f"AI Video Clipper web app: http://{host}:{port}")
    server.serve_forever()
