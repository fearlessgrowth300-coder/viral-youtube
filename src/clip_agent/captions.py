from __future__ import annotations

import textwrap
import html
from pathlib import Path

from .models import ClipCandidate, TranscriptSegment


def clean_caption(value: str) -> str:
    value = html.unescape(value)
    value = value.replace(">>", " ")
    value = " ".join(value.split())
    return value.strip()


def format_srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    millis = int(round((seconds - int(seconds)) * 1000))
    whole = int(seconds)
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    if millis == 1000:
        secs += 1
        millis = 0
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def clip_segments(
    segments: list[TranscriptSegment], start: float, end: float
) -> list[TranscriptSegment]:
    clipped: list[TranscriptSegment] = []
    for segment in segments:
        if segment.end <= start or segment.start >= end:
            continue
        clipped.append(
            TranscriptSegment(
                start=max(0.0, segment.start - start),
                end=max(0.25, min(segment.end, end) - start),
                text=clean_caption(segment.text),
            )
        )
    return clipped


def fallback_caption_segments(candidate: ClipCandidate) -> list[TranscriptSegment]:
    text = clean_caption(candidate.caption or candidate.title)
    lines = textwrap.wrap(text, width=26) or [candidate.title]
    duration = max(1.0, candidate.end - candidate.start)
    slice_duration = max(1.0, duration / max(1, len(lines)))
    output: list[TranscriptSegment] = []
    cursor = 0.0
    for line in lines:
        output.append(
            TranscriptSegment(start=cursor, end=min(duration, cursor + slice_duration), text=line)
        )
        cursor += slice_duration
    return output


def write_srt(
    path: Path,
    candidate: ClipCandidate,
    transcript_segments: list[TranscriptSegment],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    segments = clip_segments(transcript_segments, candidate.start, candidate.end)
    if not segments:
        segments = fallback_caption_segments(candidate)

    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        cleaned = clean_caption(segment.text)
        text = "\n".join(textwrap.wrap(cleaned, width=28))
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}",
                    text,
                ]
            )
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return path
