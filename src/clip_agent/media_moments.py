from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from typing import Callable

from .ffmpeg import ffmpeg_bin
from .models import ClipCandidate
from .scoring import fallback_candidates, overlaps


@dataclass(frozen=True)
class MediaPoint:
    time: float
    score: float
    reason: str


AUDIO_FRAME_RE = re.compile(r"pts_time:(?P<time>\d+(?:\.\d+)?)")
AUDIO_RMS_RE = re.compile(r"RMS_level=(?P<level>-?\d+(?:\.\d+)?|-inf)")
SCENE_TIME_RE = re.compile(r"pts_time:(?P<time>\d+(?:\.\d+)?)")
ScanProgress = Callable[[int, str], None]
MAX_SCAN_WINDOWS = 8
SCAN_WINDOW_SECONDS = 45.0


def select_media_moment_candidates(
    source: str,
    duration: float,
    max_clips: int,
    clip_length: int,
    genre: str = "auto",
    progress: ScanProgress | None = None,
) -> list[ClipCandidate]:
    windows = analysis_windows(duration)
    audio_levels = read_audio_levels(source, windows=windows, progress=progress)
    scene_times = read_scene_times(source, windows=windows, progress=progress)
    candidates = media_candidates_from_features(
        audio_levels,
        scene_times,
        duration,
        max_clips,
        clip_length,
        genre=genre,
    )
    return candidates or fallback_candidates(duration, max_clips, clip_length)


def analysis_windows(duration: float) -> list[tuple[float, float]]:
    if duration <= 0:
        return [(0.0, SCAN_WINDOW_SECONDS)]
    if duration <= SCAN_WINDOW_SECONDS * MAX_SCAN_WINDOWS:
        return [(0.0, duration)]
    latest_start = max(0.0, duration - SCAN_WINDOW_SECONDS)
    return [
        (
            round(latest_start * index / (MAX_SCAN_WINDOWS - 1), 2),
            SCAN_WINDOW_SECONDS,
        )
        for index in range(MAX_SCAN_WINDOWS)
    ]


def read_audio_levels(
    source: str,
    windows: list[tuple[float, float]] | None = None,
    progress: ScanProgress | None = None,
) -> list[tuple[float, float]]:
    windows = windows or [(0.0, SCAN_WINDOW_SECONDS)]
    levels: list[tuple[float, float]] = []
    for index, (start, length) in enumerate(windows, start=1):
        if progress:
            progress(
                50 + int(index / len(windows) * 3),
                f"Scanning audio peaks {index}/{len(windows)}",
            )
        command = [
            ffmpeg_bin(),
            "-hide_banner",
            "-nostats",
            "-ss",
            str(start),
            "-t",
            str(length),
            "-i",
            source,
            "-vn",
            "-af",
            "asetnsamples=n=44100,astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            continue
        output = f"{result.stdout}\n{result.stderr}"
        levels.extend(
            (start + time, level)
            for time, level in parse_audio_levels(output)
        )
    return levels


def read_scene_times(
    source: str,
    windows: list[tuple[float, float]] | None = None,
    progress: ScanProgress | None = None,
) -> list[float]:
    windows = windows or [(0.0, SCAN_WINDOW_SECONDS)]
    times: list[float] = []
    for index, (start, length) in enumerate(windows, start=1):
        if progress:
            progress(
                53 + int(index / len(windows) * 2),
                f"Scanning visual changes {index}/{len(windows)}",
            )
        command = [
            ffmpeg_bin(),
            "-hide_banner",
            "-nostats",
            "-ss",
            str(start),
            "-t",
            str(length),
            "-i",
            source,
            "-vf",
            "select='gt(scene,0.18)',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            continue
        output = f"{result.stdout}\n{result.stderr}"
        times.extend(
            start + time
            for time in parse_scene_times(output)
        )
    return sorted(set(round(time, 2) for time in times))


def parse_audio_levels(output: str) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    current_time: float | None = None
    for line in output.splitlines():
        frame_match = AUDIO_FRAME_RE.search(line)
        if frame_match:
            current_time = float(frame_match.group("time"))
            continue
        level_match = AUDIO_RMS_RE.search(line)
        if level_match and current_time is not None:
            raw_level = level_match.group("level")
            if raw_level == "-inf":
                continue
            levels.append((current_time, float(raw_level)))
            current_time = None
    return levels


def parse_scene_times(output: str) -> list[float]:
    times: list[float] = []
    for line in output.splitlines():
        if "Parsed_showinfo" not in line:
            continue
        match = SCENE_TIME_RE.search(line)
        if match:
            times.append(float(match.group("time")))
    return sorted(set(round(time, 2) for time in times))


def media_candidates_from_features(
    audio_levels: list[tuple[float, float]],
    scene_times: list[float],
    duration: float,
    max_clips: int,
    clip_length: int,
    genre: str = "auto",
) -> list[ClipCandidate]:
    duration = duration or infer_duration(audio_levels, scene_times, max_clips * clip_length)
    if duration <= 0:
        return []
    points = score_media_points(audio_levels, scene_times, duration)
    if not points:
        return []

    selected: list[ClipCandidate] = []
    for point in sorted(points, key=lambda item: item.score, reverse=True):
        candidate = candidate_from_media_point(
            point,
            duration,
            clip_length,
            len(selected) + 1,
            genre=genre,
        )
        if overlaps(candidate, selected):
            continue
        selected.append(candidate)
        if len(selected) >= max_clips:
            break

    if len(selected) < max_clips:
        for fallback in fallback_candidates(duration, max_clips - len(selected), clip_length):
            if not overlaps(fallback, selected):
                selected.append(fallback)
    return sorted(selected[:max_clips], key=lambda item: item.start)


def score_media_points(
    audio_levels: list[tuple[float, float]],
    scene_times: list[float],
    duration: float,
) -> list[MediaPoint]:
    points: list[MediaPoint] = []
    previous_level: float | None = None
    intro_cutoff = 8.0 if duration > 90 else 0.0
    for time, rms_level in audio_levels:
        if time < intro_cutoff:
            previous_level = rms_level
            continue
        loudness = clamp((rms_level + 55.0) / 35.0)
        change = 0.0 if previous_level is None else clamp(abs(rms_level - previous_level) / 18.0)
        nearest_scene = min((abs(time - scene) for scene in scene_times), default=99.0)
        scene_bonus = 1.2 if nearest_scene <= 2.0 else 0.0
        score = loudness * 7.0 + change * 2.5 + scene_bonus
        if score > 1.0:
            reason = f"audio peak {rms_level:.1f} dB"
            if scene_bonus:
                reason += ", near scene change"
            points.append(MediaPoint(time=time, score=round(score, 3), reason=reason))
        previous_level = rms_level

    for scene_time in scene_times:
        if scene_time >= intro_cutoff:
            points.append(MediaPoint(time=scene_time, score=3.4, reason="visual scene change"))
    return points


def candidate_from_media_point(
    point: MediaPoint,
    duration: float,
    clip_length: int,
    rank: int,
    genre: str = "auto",
) -> ClipCandidate:
    start = max(0.0, point.time - clip_length * 0.38)
    if duration:
        start = min(start, max(0.0, duration - clip_length))
    end = min(duration or start + clip_length, start + clip_length)
    title = media_title(rank, genre)
    hook = media_hook(rank, genre)
    return ClipCandidate(
        start=round(start, 2),
        end=round(end, 2),
        title=title,
        reason=f"Media score {point.score:.2f}: selected around {format_time(point.time)} from {point.reason}.",
        score=point.score,
        kind="high-energy",
        caption=hook,
        voiceover=f"{title}.",
        hook=hook,
        description=f"{hook}. High-energy clip selected from audio and visual activity around {format_time(point.time)}.",
        tags=("shorts", "viral", "bestmoments", "highlight"),
        engagement_question="Did you expect the reaction to peak here?",
    )


def media_title(rank: int, genre: str) -> str:
    by_genre = {
        "funny": ["Big Reaction Moment", "The Funny Part", "Unexpected Reaction"],
        "crazy": ["The Moment It Spiked", "This Turn Got Intense", "High-Energy Twist"],
        "feel-good": ["The Feel-Good Moment", "The Part That Lands", "Uplifting Highlight"],
        "business": ["The Biggest Reveal", "The Scale Moment", "The Key Business Point"],
        "sports": ["The High-Pressure Moment", "The Big Play", "Game-Changing Highlight"],
    }
    defaults = ["High-Energy Moment", "The Moment It Picked Up", "Big Reaction Clip", "The Strongest Turn"]
    titles = by_genre.get(genre, defaults)
    title = titles[(rank - 1) % len(titles)]
    return title if rank <= len(titles) else f"{title} {rank}"


def media_hook(rank: int, genre: str) -> str:
    by_genre = {
        "funny": ["WAIT FOR THE REACTION", "THE LAUGH HAPPENS HERE", "DON'T MISS THE REACTION"],
        "crazy": ["THIS SPIKES FAST", "THE TURN HAPPENS HERE", "DON'T MISS THE SWITCH"],
        "feel-good": ["THIS PART PAYS OFF", "THE GOOD MOMENT IS HERE", "KEEP WATCHING FOR THE TURN"],
        "business": ["LOOK HOW BIG THIS GETS", "THE SCALE SHOWS UP HERE", "THIS PART EXPLAINS THE MONEY"],
        "sports": ["THE PRESSURE HITS HERE", "THE BIG PLAY BUILDS HERE", "DON'T MISS THE MOVE"],
    }
    defaults = ["THE TURN HAPPENS HERE", "IT PICKS UP RIGHT HERE", "KEEP WATCHING FOR THE PEAK", "DON'T MISS THE SWITCH"]
    hooks = by_genre.get(genre, defaults)
    return hooks[(rank - 1) % len(hooks)]


def infer_duration(audio_levels: list[tuple[float, float]], scene_times: list[float], fallback: float) -> float:
    values = [time for time, _ in audio_levels] + scene_times
    return max(values) if values else fallback


def format_time(seconds: float) -> str:
    if not math.isfinite(seconds):
        return "0:00"
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))
