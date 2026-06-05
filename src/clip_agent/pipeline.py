from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .ai_select import refine_candidates_with_openai
from .config import AgentConfig
from .ffmpeg import probe_duration
from .media_moments import select_media_moment_candidates
from .models import ClipCandidate, PipelineResult
from .publishers.dispatcher import append_publish_queue
from .render import render_clip
from .scoring import select_candidates
from .source import prepare_source
from .transcribe import get_transcript


ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class RunOptions:
    source: str
    out_dir: Path
    transcript_path: Path | None = None
    max_clips: int = 3
    clip_length_seconds: int = 45
    vertical: bool = True
    enable_voiceover: bool = False
    auto_hook: bool = True
    clip_model: str = "viral"
    genre: str = "auto"
    caption_style: str = "karaoke"
    render_quality: str = "2k"
    live_capture_seconds: int = 300
    platforms: tuple[str, ...] = ()
    max_transcribe_seconds: int | None = None


def source_slug(source: str) -> str:
    parsed = urlparse(source)
    if parsed.scheme and parsed.netloc:
        raw = f"{parsed.netloc}-{parsed.path.strip('/')}"
    else:
        raw = Path(source).stem
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip(".-_")
    return safe[:48] or "source"


def create_run_dir(out_dir: Path, source: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = out_dir / f"{timestamp}-{source_slug(source)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def emit_progress(
    progress: ProgressCallback | None,
    phase: str,
    percent: int,
    message: str,
) -> None:
    if progress:
        progress({"phase": phase, "progress": max(0, min(100, percent)), "message": message})


def clamp_candidate_length(candidate: ClipCandidate, max_seconds: int, duration: float) -> ClipCandidate:
    max_seconds = max(5, int(max_seconds or 45))
    if candidate.end - candidate.start <= max_seconds:
        return candidate
    end = candidate.start + max_seconds
    if duration:
        end = min(duration, end)
    if end <= candidate.start:
        return candidate
    return replace(candidate, end=round(end, 2))


def run_once(
    options: RunOptions,
    config: AgentConfig,
    progress: ProgressCallback | None = None,
) -> PipelineResult:
    run_dir = create_run_dir(options.out_dir, options.source)
    emit_progress(progress, "setup", 2, "Created run folder")
    result = PipelineResult(run_dir=run_dir, source=options.source)
    source = prepare_source(
        options.source,
        run_dir,
        config,
        progress=progress,
        live_capture_seconds=options.live_capture_seconds,
    )
    emit_progress(progress, "probe", 35, "Checking source duration")
    duration = probe_duration(source)
    emit_progress(progress, "transcript", 44, "Reading captions or transcribing")
    transcript = get_transcript(
        source,
        run_dir,
        config,
        transcript_path=options.transcript_path,
        max_seconds=options.max_transcribe_seconds,
    )
    if transcript:
        emit_progress(progress, "select", 56, "Selecting viral transcript moments")
        candidates = select_candidates(
            transcript,
            duration,
            options.max_clips,
            options.clip_length_seconds,
            genre=options.genre,
        )
    else:
        emit_progress(progress, "select", 50, "Scanning video/audio peaks")
        candidates = select_media_moment_candidates(
            source,
            duration,
            options.max_clips,
            options.clip_length_seconds,
            genre=options.genre,
        )
        emit_progress(progress, "select", 56, "Selected high-energy moments")
    candidates = refine_candidates_with_openai(candidates, transcript, duration, config)
    candidates = [
        clamp_candidate_length(candidate, options.clip_length_seconds, duration)
        for candidate in candidates
    ]

    clips_dir = run_dir / "clips"
    total_candidates = max(1, len(candidates))
    for index, candidate in enumerate(candidates, start=1):
        render_start = 58 + int((index - 1) / total_candidates * 36)
        render_end = 58 + int(index / total_candidates * 36)
        emit_progress(
            progress,
            "render",
            render_start,
            f"Rendering clip {index}/{len(candidates)}",
        )
        try:
            result.clips.append(
                render_clip(
                    source,
                    candidate,
                    transcript,
                    clips_dir,
                    config,
                    vertical=options.vertical,
                    enable_voiceover=options.enable_voiceover,
                    auto_hook=options.auto_hook,
                    caption_style=options.caption_style,
                    render_quality=options.render_quality,
                    progress=progress,
                    progress_start=render_start,
                    progress_end=render_end,
                    progress_message=f"Rendering clip {index}/{len(candidates)}",
                )
            )
            emit_progress(
                progress,
                "render",
                58 + int(index / total_candidates * 36),
                f"Rendered clip {index}/{len(candidates)}",
            )
        except Exception as exc:
            result.skipped.append(f"{candidate.title}: {exc}")

    emit_progress(progress, "queue", 97, "Writing manifest and publish queue")
    manifest = result.to_jsonable()
    manifest["duration"] = duration
    manifest["transcript_segments"] = len(transcript)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    append_publish_queue(run_dir / "publish_queue.jsonl", result.clips, list(options.platforms))
    emit_progress(progress, "done", 100, f"Rendered {len(result.clips)} clip(s)")
    return result


def load_processed(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8") or "[]"))


def save_processed(path: Path, processed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(processed), indent=2), encoding="utf-8")


def watch_directory(
    watch_dir: Path,
    out_dir: Path,
    config: AgentConfig,
    interval: int,
    max_clips: int,
    clip_length_seconds: int,
    platforms: tuple[str, ...],
    once: bool = False,
) -> None:
    state_path = out_dir / ".worker_processed.json"
    processed = load_processed(state_path)
    watch_dir.mkdir(parents=True, exist_ok=True)
    while True:
        videos = sorted(
            path
            for path in watch_dir.iterdir()
            if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
        )
        for video in videos:
            key = str(video.resolve())
            if key in processed:
                continue
            options = RunOptions(
                source=key,
                out_dir=out_dir,
                max_clips=max_clips,
                clip_length_seconds=clip_length_seconds,
                platforms=platforms,
            )
            run_once(options, config)
            processed.add(key)
            save_processed(state_path, processed)
        if once:
            return
        time.sleep(interval)
