from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .ai_select import (
    analyze_transcript_for_viral_moments,
    cached_analysis_matches_media,
    candidates_from_viral_analysis,
    load_cached_viral_analysis,
)
from .config import AgentConfig
from .ffmpeg import probe_duration
from .media_moments import select_media_moment_candidates
from .models import ClipCandidate, PipelineResult
from .publishers.dispatcher import append_publish_queue
from .render import render_clip
from .scoring import select_candidates
from .seo import optimize_candidate
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
    caption_style: str = "bold"
    render_quality: str = "2k"
    live_capture_seconds: int = 300
    platforms: tuple[str, ...] = ()
    max_transcribe_seconds: int | None = None
    generation_mode: str = "short"
    interaction_prompt: bool = True
    enable_broll: bool = True
    polish: bool = True
    seo_optimize: bool = True
    voice_provider: str = "local-piper"
    narration_style: str = "movie-recap"


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


def prepare_long_form_candidate(
    candidate: ClipCandidate,
    requested_seconds: int,
    duration: float,
) -> ClipCandidate:
    requested_seconds = max(60, requested_seconds)
    if duration and requested_seconds >= duration:
        start = 0.0
        end = duration
    else:
        start = candidate.start
        end = min(duration or start + requested_seconds, start + requested_seconds)
    tags = tuple(
        dict.fromkeys(
            [tag for tag in candidate.tags if tag not in {"shorts", "bestmoments"}]
            + ["longform", "highlight"]
        )
    )
    title = candidate.title
    if "highlight" not in title.lower():
        title = f"{title} - Extended Highlight"
    return replace(
        candidate,
        start=round(start, 2),
        end=round(end, 2),
        title=title[:100],
        tags=tags,
    )


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
        source_url=options.source,
    )
    viral_analysis: dict[str, Any] = {}
    if transcript:
        emit_progress(progress, "select", 52, "Ranking viral transcript moments")
        source_analysis = load_cached_viral_analysis(
            options.source,
            model=config.openai_analysis_model if config.openai_api_key else None,
        )
        if source_analysis and cached_analysis_matches_media(source_analysis, duration):
            viral_analysis = {**source_analysis, "cached": True}
            emit_progress(progress, "select", 54, "Using the OpenAI moments shown before rendering")
        else:
            viral_analysis = analyze_transcript_for_viral_moments(
                options.source,
                transcript,
                duration,
                config,
                max_moments=max(10, options.max_clips),
                cache_result=source_analysis is None,
            )
        candidates = candidates_from_viral_analysis(viral_analysis, duration)
        if candidates:
            candidates = candidates[: options.max_clips]
            provider = str(viral_analysis.get("provider") or "transcript analysis")
            emit_progress(progress, "select", 56, f"Selected viral moments with {provider}")
        else:
            candidates = select_candidates(
                transcript,
                duration,
                options.max_clips,
                options.clip_length_seconds,
                genre=options.genre,
            )
            emit_progress(progress, "select", 56, "Selected transcript moments")
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
    candidates = [
        clamp_candidate_length(candidate, options.clip_length_seconds, duration)
        for candidate in candidates
    ]
    if options.generation_mode == "long":
        candidates = [
            prepare_long_form_candidate(
                candidates[0],
                options.clip_length_seconds,
                duration,
            )
        ] if candidates else []
    if options.seo_optimize:
        candidates = [
            optimize_candidate(candidate, options.generation_mode)
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
                    interaction_prompt=options.interaction_prompt,
                    enable_broll=options.enable_broll,
                    polish=options.polish,
                    voice_provider=options.voice_provider,
                    narration_style=options.narration_style,
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
    manifest["generation_mode"] = options.generation_mode
    manifest["viral_analysis_provider"] = viral_analysis.get("provider", "")
    manifest["viral_analysis_cached"] = bool(viral_analysis.get("cached"))
    manifest["enhancements"] = {
        "bold_captions": True,
        "broll": options.enable_broll,
        "polish": options.polish,
        "seo_optimizer": options.seo_optimize,
        "voice_provider": options.voice_provider if options.enable_voiceover else "",
        "narration_style": options.narration_style if options.enable_voiceover else "",
    }
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
