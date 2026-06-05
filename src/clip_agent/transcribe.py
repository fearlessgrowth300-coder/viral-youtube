from __future__ import annotations

import json
import html
import re
import subprocess
from pathlib import Path

from .config import AgentConfig
from .ffmpeg import ffmpeg_bin, probe_duration
from .models import TranscriptSegment


SRT_BLOCK_RE = re.compile(
    r"\d+\s+"
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})\s+"
    r"(?P<text>.*?)(?=\n\s*\n|\Z)",
    re.DOTALL,
)

VTT_CUE_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})(?:[^\n]*)\n"
    r"(?P<text>.*?)(?=\n\s*\n|\Z)",
    re.DOTALL,
)


def parse_srt_time(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis) / 1000
    )


def parse_vtt_time(value: str) -> float:
    parts = value.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, rest = parts
    else:
        hours, minutes, rest = parts
    seconds, millis = rest.split(".")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def clean_caption_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace(">>", " ")
    lines = []
    for line in value.splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if clean and clean not in lines:
            lines.append(clean)
    return " ".join(lines).strip()


def load_srt(path: Path) -> list[TranscriptSegment]:
    text = path.read_text(encoding="utf-8-sig")
    segments: list[TranscriptSegment] = []
    for match in SRT_BLOCK_RE.finditer(text):
        body = html.unescape(" ".join(line.strip() for line in match.group("text").splitlines()))
        body = re.sub(r"<[^>]+>", "", body).strip()
        body = body.replace(">>", " ").strip()
        if body:
            segments.append(
                TranscriptSegment(
                    start=parse_srt_time(match.group("start")),
                    end=parse_srt_time(match.group("end")),
                    text=body,
                )
            )
    return segments


def load_vtt(path: Path) -> list[TranscriptSegment]:
    text = path.read_text(encoding="utf-8-sig")
    segments: list[TranscriptSegment] = []
    for match in VTT_CUE_RE.finditer(text):
        body = clean_caption_text(match.group("text"))
        if body:
            segments.append(
                TranscriptSegment(
                    start=parse_vtt_time(match.group("start")),
                    end=parse_vtt_time(match.group("end")),
                    text=body,
                )
            )
    return segments


def load_transcript_file(path: Path) -> list[TranscriptSegment]:
    suffix = path.suffix.lower()
    if suffix == ".srt":
        return load_srt(path)
    if suffix == ".vtt":
        return load_vtt(path)
    raise ValueError(f"Unsupported transcript file type: {path}")


def align_segments_to_source(
    segments: list[TranscriptSegment],
    source: str,
) -> list[TranscriptSegment]:
    if not segments:
        return segments
    try:
        duration = probe_duration(source)
    except Exception:
        return segments
    if duration <= 0:
        return segments
    first_start = min(segment.start for segment in segments)
    last_end = max(segment.end for segment in segments)
    if first_start <= duration and last_end <= duration * 1.5:
        return segments
    shifted: list[TranscriptSegment] = []
    for segment in segments:
        start = max(0.0, segment.start - first_start)
        end = max(start + 0.2, segment.end - first_start)
        if start <= duration:
            shifted.append(
                TranscriptSegment(
                    start=round(start, 3),
                    end=round(min(duration, end), 3),
                    text=segment.text,
                )
            )
    return shifted


def find_sidecar_transcript(source: str) -> Path | None:
    source_path = Path(source)
    if not source_path.exists():
        return None
    candidates: list[Path] = []
    for suffix in ("*.srt", "*.vtt"):
        candidates.extend(source_path.parent.glob(f"{source_path.stem}*{suffix[1:]}"))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.suffix.lower() != ".srt", len(item.name)))[0]


def extract_audio(source: str, destination: Path, seconds: int | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    args = [
        ffmpeg_bin(),
        "-y",
        "-i",
        source,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "48k",
    ]
    if seconds:
        args.extend(["-t", str(seconds)])
    args.append(str(destination))
    subprocess.run(args, check=True, capture_output=True, text=True)
    return destination


def response_to_dict(response: object) -> dict:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, dict):
        return response
    return json.loads(json.dumps(response, default=lambda value: getattr(value, "__dict__", str(value))))


def split_text_into_segments(text: str, duration: float) -> list[TranscriptSegment]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    if not sentences:
        return []
    duration = duration or max(8.0, len(sentences) * 6.0)
    slice_duration = duration / len(sentences)
    return [
        TranscriptSegment(
            start=index * slice_duration,
            end=min(duration, (index + 1) * slice_duration),
            text=sentence,
        )
        for index, sentence in enumerate(sentences)
    ]


def transcribe_with_openai(
    source: str, run_dir: Path, config: AgentConfig, max_seconds: int | None = None
) -> list[TranscriptSegment]:
    if not config.openai_api_key:
        return []

    audio_path = extract_audio(source, run_dir / "source_audio.mp3", max_seconds)
    if audio_path.stat().st_size > 24_500_000:
        return []

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError('Install the ai extra: python -m pip install -e ".[ai]"') from exc

    client = OpenAI(api_key=config.openai_api_key)
    with audio_path.open("rb") as audio_file:
        try:
            response = client.audio.transcriptions.create(
                model=config.openai_transcribe_model,
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        except Exception:
            audio_file.seek(0)
            response = client.audio.transcriptions.create(
                model=config.openai_transcribe_model,
                file=audio_file,
                response_format="json",
            )

    payload = response_to_dict(response)
    segments = payload.get("segments") or []
    if segments:
        return [
            TranscriptSegment(
                start=float(item.get("start", 0.0)),
                end=float(item.get("end", 0.0)),
                text=str(item.get("text", "")).strip(),
            )
            for item in segments
            if str(item.get("text", "")).strip()
        ]

    text = str(payload.get("text", "")).strip()
    if not text:
        return []
    return split_text_into_segments(text, probe_duration(source))


def get_transcript(
    source: str,
    run_dir: Path,
    config: AgentConfig,
    transcript_path: Path | None = None,
    max_seconds: int | None = None,
) -> list[TranscriptSegment]:
    if transcript_path:
        return load_transcript_file(transcript_path)
    sidecar = find_sidecar_transcript(source)
    if sidecar:
        return align_segments_to_source(load_transcript_file(sidecar), source)
    return transcribe_with_openai(source, run_dir, config, max_seconds=max_seconds)
