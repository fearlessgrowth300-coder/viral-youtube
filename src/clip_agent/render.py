from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .captions import write_srt
from .config import AgentConfig
from .ffmpeg import escape_filter_path, ffmpeg_bin
from .models import ClipCandidate, RenderedClip, TranscriptSegment
from .scoring import build_hook, is_generic_hook
from .thumbnail import generate_viral_thumbnail, thumbnail_headline
from .voiceover import PIPER_ATTRIBUTION, generate_voiceover_with_provider

ProgressCallback = Callable[[dict[str, Any]], None]


def slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return clean[:60] or "clip"


CAPTION_PRESETS = {
    "bold": (
        "FontName=Arial Black,FontSize=12,Bold=1,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2.2,Shadow=0,Alignment=2,MarginV=40"
    ),
    "clean": (
        "FontName=Arial,FontSize=10,Bold=1,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=1.4,Shadow=0,Alignment=2,MarginV=38"
    ),
    "karaoke": (
        "FontName=Arial Black,FontSize=11,Bold=1,PrimaryColour=&H0000FF00,SecondaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=1.8,Shadow=0,Alignment=2,MarginV=40"
    ),
    "mozi": (
        "FontName=Arial Black,FontSize=12,Bold=1,PrimaryColour=&H0000FF00,SecondaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2.0,Shadow=0,Alignment=2,MarginV=42"
    ),
    "popline": (
        "FontName=Arial Black,FontSize=11,Bold=1,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=1.8,Shadow=0,Alignment=2,MarginV=40"
    ),
    "yellow": (
        "FontName=Arial Black,FontSize=11,Bold=1,PrimaryColour=&H0000FFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=1.8,Shadow=0,Alignment=2,MarginV=40"
    ),
    "white-box": (
        "FontName=Arial,FontSize=10,Bold=1,PrimaryColour=&H00000000,"
        "BackColour=&H00FFFFFF,OutlineColour=&H00FFFFFF,"
        "BorderStyle=3,Outline=1.0,Shadow=0,Alignment=2,MarginV=40"
    ),
}

QUALITY_SETTINGS = {
    "hd": {
        "vertical": (1080, 1920),
        "horizontal": (1920, 1080),
        "crf": "21",
        "preset": "faster",
        "unsharp": "unsharp=5:5:0.45:3:3:0.20",
    },
    "2k": {
        "vertical": (1440, 2560),
        "horizontal": (2560, 1440),
        "crf": "19",
        "preset": "slow",
        "unsharp": "unsharp=5:5:0.60:3:3:0.25",
    },
    "4k": {
        "vertical": (2160, 3840),
        "horizontal": (3840, 2160),
        "crf": "18",
        "preset": "slow",
        "unsharp": "unsharp=5:5:0.75:3:3:0.30",
    },
}


def render_quality_settings(render_quality: str) -> dict[str, object]:
    key = str(render_quality or "hd").lower()
    return QUALITY_SETTINGS.get(key, QUALITY_SETTINGS["hd"])


def render_dimensions(vertical: bool, render_quality: str) -> tuple[int, int]:
    settings = render_quality_settings(render_quality)
    return settings["vertical" if vertical else "horizontal"]  # type: ignore[return-value]


def escape_drawtext(value: str, limit: int = 64) -> str:
    clean = re.sub(r"[ \t\r\f\v]+", " ", value).strip()
    clean = re.sub(r"\n\s*", "\n", clean)
    clean = clean.replace("'", "").replace("’", "").replace("‘", "")
    clean = (
        clean.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(":", "\\:")
        .replace('"', "")
        .replace(",", "\\,")
        .replace("%", "\\%")
    )
    clean = re.sub(r"[^\x20-\x7E\n]+", "", clean)
    clean = clean.replace("'", "").replace("\\n", "\n")
    return clean[:limit]


def wrap_text(value: str, line_length: int = 22, max_lines: int = 2) -> str:
    words = re.sub(r"\s+", " ", value).strip().split()
    lines: list[str] = []
    current = ""
    for word in words:
        next_line = f"{current} {word}".strip()
        if current and len(next_line) > line_length:
            lines.append(current)
            current = word
        else:
            current = next_line
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return "\n".join(lines[:max_lines])


def parse_ffmpeg_progress_seconds(line: str) -> float | None:
    if line.startswith("out_time_ms="):
        try:
            return float(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    if line.startswith("out_time="):
        parts = line.split("=", 1)[1].strip().split(":")
        if len(parts) != 3:
            return None
        try:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            return None
    return None


def run_ffmpeg_render(
    args: list[str],
    clip_duration: float,
    progress: ProgressCallback | None = None,
    progress_start: int = 0,
    progress_end: int = 100,
    progress_message: str = "Rendering clip",
) -> None:
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    log_tail: list[str] = []
    last_emit = 0.0
    try:
        for line in process.stdout:
            clean = line.rstrip()
            if clean:
                log_tail.append(clean)
                log_tail = log_tail[-80:]
            elapsed = parse_ffmpeg_progress_seconds(clean)
            if progress and elapsed is not None:
                ratio = max(0.0, min(1.0, elapsed / max(1.0, clip_duration)))
                percent = progress_start + int((progress_end - progress_start) * ratio)
                if percent > last_emit:
                    progress(
                        {
                            "phase": "render",
                            "progress": percent,
                            "message": f"{progress_message} {int(ratio * 100)}%",
                        }
                    )
                    last_emit = float(percent)
    except Exception:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    returncode = process.wait()
    if returncode != 0:
        raise RuntimeError("\n".join(log_tail)[-2000:] or f"FFmpeg failed with exit code {returncode}.")


def retention_hook_text(candidate: ClipCandidate) -> str:
    hook = candidate.hook or candidate.caption or candidate.title
    if not hook or is_generic_hook(hook):
        reason = re.sub(r"^Viral score\s+[\d.]+:\s*", "", candidate.reason or "", flags=re.I)
        source_text = reason or candidate.title
        hook = build_hook(source_text, 1)
    return hook


def hook_filter(candidate: ClipCandidate, render_quality: str = "hd", vertical: bool = True) -> str:
    width, height = render_dimensions(vertical, render_quality)
    scale = min(width / 1080, height / 1920) if vertical else min(width / 1920, height / 1080)
    fontsize = max(40, int(round(50 * scale)))
    boxborder = max(12, int(round(18 * scale)))
    y = max(52, int(round(86 * scale)))
    hook = escape_drawtext(wrap_text(retention_hook_text(candidate), line_length=17, max_lines=3), limit=76)
    if not hook:
        return ""
    return (
        "drawtext="
        f"text='{hook}':"
        f"x=(w-text_w)/2:y={y}:"
        f"font='Arial':fontsize={fontsize}:fontcolor=black:"
        f"box=1:boxcolor=white@0.95:boxborderw={boxborder}:"
        "borderw=0:"
        "text_align=center:"
        "line_spacing=8:"
        "enable='between(t,0,5)'"
    )


def engagement_filter(
    candidate: ClipCandidate,
    render_quality: str = "hd",
    vertical: bool = True,
) -> str:
    question = candidate.engagement_question.strip()
    if not question:
        return ""
    width, height = render_dimensions(vertical, render_quality)
    scale = min(width / 1080, height / 1920) if vertical else min(width / 1920, height / 1080)
    fontsize = max(32, int(round(38 * scale)))
    boxborder = max(10, int(round(14 * scale)))
    wrapped = wrap_text(question.upper(), line_length=24 if vertical else 42, max_lines=2)
    text = escape_drawtext(wrapped, limit=100)
    clip_duration = max(1.0, candidate.end - candidate.start)
    show_from = max(5.0, clip_duration - 8.0)
    return (
        "drawtext="
        f"text='{text}':"
        "x=(w-text_w)/2:y=h*0.72-text_h/2:"
        f"font='Arial':fontsize={fontsize}:fontcolor=white:"
        f"box=1:boxcolor=black@0.82:boxborderw={boxborder}:"
        "borderw=0:"
        "text_align=center:"
        "line_spacing=6:"
        f"enable='between(t,{show_from:.2f},{clip_duration:.2f})'"
    )


def subtitle_filter(srt_path: Path, caption_style: str) -> str:
    style = caption_style if caption_style in CAPTION_PRESETS else "bold"
    preset = CAPTION_PRESETS[style]
    return f"subtitles='{escape_filter_path(srt_path)}':force_style='{preset}'"


def broll_enable_expression(clip_duration: float) -> str:
    if clip_duration < 8:
        return ""
    windows: list[tuple[float, float]] = []
    first_start = min(max(5.5, clip_duration * 0.24), clip_duration - 2.5)
    windows.append((first_start, min(clip_duration, first_start + 2.8)))
    if clip_duration >= 20:
        second_start = min(clip_duration * 0.62, clip_duration - 2.5)
        windows.append((second_start, min(clip_duration, second_start + 2.8)))
    return "+".join(f"between(t,{start:.2f},{end:.2f})" for start, end in windows)


def polish_filters(enabled: bool) -> str:
    if not enabled:
        return ""
    return "eq=contrast=1.04:saturation=1.10:brightness=0.01,hqdn3d=1.0:1.0:3.0:3.0"


def video_filter(
    srt_path: Path,
    vertical: bool,
    candidate: ClipCandidate,
    auto_hook: bool,
    caption_style: str = "bold",
    render_quality: str = "hd",
    interaction_prompt: bool = True,
    enable_broll: bool = True,
    polish: bool = True,
) -> str:
    settings = render_quality_settings(render_quality)
    width, height = render_dimensions(vertical, render_quality)
    clip_duration = max(1.0, candidate.end - candidate.start)
    broll_enable = broll_enable_expression(clip_duration) if enable_broll else ""
    overlays = [
        item
        for item in (
            subtitle_filter(srt_path, caption_style),
            hook_filter(candidate, render_quality, vertical) if auto_hook else "",
            engagement_filter(candidate, render_quality, vertical) if interaction_prompt else "",
        )
        if item
    ]
    overlays.extend([str(settings["unsharp"]), "setsar=1", "format=yuv420p"])
    polish_chain = polish_filters(polish)
    if vertical:
        source_prefix = f"[0:v]{polish_chain}," if polish_chain else "[0:v]"
        if broll_enable:
            return (
                f"{source_prefix}split=3[mainbg][mainfg][cutaway];"
                f"[mainbg]scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{height},boxblur=24:1[bg];"
                f"[mainfg]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
                "[cutaway]crop=iw*0.82:ih*0.82:(iw-iw*0.82)/2:(ih-ih*0.82)/2,"
                f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{height}[broll];"
                f"[base][broll]overlay=0:0:enable='{broll_enable}'[scene];"
                f"[scene]{','.join(overlays)}[v]"
            )
        return (
            f"{source_prefix}split=2[mainbg][mainfg];"
            f"[mainbg]scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height},boxblur=24:1[bg];"
            f"[mainfg]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{','.join(overlays)}[v]"
        )
    source_prefix = f"[0:v]{polish_chain}," if polish_chain else "[0:v]"
    if broll_enable:
        return (
            f"{source_prefix}split=2[main][cutaway];"
            f"[main]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[base];"
            "[cutaway]crop=iw*0.82:ih*0.82:(iw-iw*0.82)/2:(ih-ih*0.82)/2,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height}[broll];"
            f"[base][broll]overlay=0:0:enable='{broll_enable}'[scene];"
            f"[scene]{','.join(overlays)}[v]"
        )
    return (
        f"{source_prefix}scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,{','.join(overlays)}[v]"
    )


def render_clip(
    source: str,
    candidate: ClipCandidate,
    transcript_segments: list[TranscriptSegment],
    output_dir: Path,
    config: AgentConfig,
    vertical: bool = True,
    enable_voiceover: bool = False,
    auto_hook: bool = True,
    caption_style: str = "bold",
    render_quality: str = "hd",
    interaction_prompt: bool = True,
    enable_broll: bool = True,
    polish: bool = True,
    voice_provider: str = "elevenlabs",
    narration_style: str = "movie-recap",
    generate_thumbnail: bool = False,
    progress: ProgressCallback | None = None,
    progress_start: int = 0,
    progress_end: int = 100,
    progress_message: str = "Rendering clip",
) -> RenderedClip:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{int(candidate.start):06d}-{slugify(candidate.title)}"
    srt_path = output_dir / f"{stem}.srt"
    metadata_path = output_dir / f"{stem}.json"
    video_path = output_dir / f"{stem}.mp4"
    thumbnail_path = output_dir / f"{stem}-thumbnail.jpg"
    voiceover_path = output_dir / f"{stem}-voiceover.mp3"

    write_srt(srt_path, candidate, transcript_segments)
    has_ai_voiceover = False
    used_voiceover_provider = ""
    generated_voiceover_path: Path | None = None
    if enable_voiceover:
        generated, used_voiceover_provider = generate_voiceover_with_provider(
            candidate,
            voiceover_path,
            config,
            provider=voice_provider,
            narration_style=narration_style,
        )
        has_ai_voiceover = generated is not None
        generated_voiceover_path = generated

    clip_duration = max(1.0, candidate.end - candidate.start)
    args = [
        ffmpeg_bin(),
        "-y",
        "-ss",
        str(candidate.start),
        "-t",
        str(clip_duration),
        "-i",
        source,
    ]

    audio_inputs: list[str] = []
    background = config.background_audio_path
    if background and background.exists():
        audio_inputs.append(str(background))
        args.extend(["-stream_loop", "-1", "-i", str(background)])
    if has_ai_voiceover:
        assert generated_voiceover_path is not None
        audio_inputs.append(str(generated_voiceover_path))
        args.extend(["-i", str(generated_voiceover_path)])

    render_settings = render_quality_settings(render_quality)
    filter_parts = [
        video_filter(
            srt_path,
            vertical,
            candidate,
            auto_hook,
            caption_style,
            render_quality,
            interaction_prompt,
            enable_broll,
            polish,
        )
    ]
    map_audio = ["-map", "0:a?"]
    if audio_inputs:
        original_volume = "0.28" if has_ai_voiceover else "1.0"
        audio_labels = [f"[0:a]volume={original_volume}[a0]"]
        input_index = 1
        if background and background.exists():
            audio_labels.append(f"[{input_index}:a]volume={config.background_volume}[bg]")
            input_index += 1
        if has_ai_voiceover:
            audio_labels.append(f"[{input_index}:a]volume=1.35[vo]")
        mix_inputs = "".join(label[label.rfind("[") :] for label in audio_labels)
        filter_parts.extend(audio_labels)
        filter_parts.append(
            f"{mix_inputs}amix=inputs={len(audio_labels)}:duration=first:dropout_transition=2[a]"
        )
        map_audio = ["-map", "[a]"]

    args.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[v]",
            *map_audio,
            "-c:v",
            "libx264",
            "-preset",
            str(render_settings["preset"]),
            "-crf",
            str(render_settings["crf"]),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            str(video_path),
        ]
    )
    try:
        run_ffmpeg_render(args, clip_duration, progress, progress_start, progress_end, progress_message)
    except Exception:
        if video_path.exists() and video_path.stat().st_size == 0:
            video_path.unlink()
        raise

    generated_thumbnail: Path | None = None
    thumbnail_error = ""
    if generate_thumbnail:
        if progress:
            progress(
                {
                    "phase": "thumbnail",
                    "progress": max(progress_start, progress_end - 1),
                    "message": "Generating viral thumbnail",
                }
            )
        try:
            generated_thumbnail = generate_viral_thumbnail(source, candidate, thumbnail_path)
        except Exception as exc:
            thumbnail_error = str(exc)

    metadata = {
        "candidate": asdict(candidate),
        "video_path": str(video_path),
        "srt_path": str(srt_path),
        "thumbnail_path": str(generated_thumbnail) if generated_thumbnail else "",
        "thumbnail_text": thumbnail_headline(candidate) if generated_thumbnail else "",
        "thumbnail_error": thumbnail_error,
        "has_ai_voiceover": has_ai_voiceover,
        "caption_style": caption_style,
        "render_quality": render_quality,
        "interaction_prompt": interaction_prompt,
        "voiceover_provider": used_voiceover_provider,
        "voiceover_attribution": (
            PIPER_ATTRIBUTION if used_voiceover_provider.startswith("Piper") else ""
        ),
        "broll_applied": bool(enable_broll and broll_enable_expression(clip_duration)),
        "broll_attribution": (
            "Source-derived cutaway from the authorized input video."
            if enable_broll and broll_enable_expression(clip_duration)
            else ""
        ),
        "polished": polish,
        "disclosure": "Contains AI-generated voiceover." if has_ai_voiceover else "",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return RenderedClip(
        video_path=video_path,
        srt_path=srt_path,
        metadata_path=metadata_path,
        candidate=candidate,
        thumbnail_path=generated_thumbnail,
        thumbnail_text=thumbnail_headline(candidate) if generated_thumbnail else "",
        has_ai_voiceover=has_ai_voiceover,
        voiceover_provider=used_voiceover_provider,
        broll_applied=bool(enable_broll and broll_enable_expression(clip_duration)),
        broll_attribution=(
            "Source-derived cutaway from the authorized input video."
            if enable_broll and broll_enable_expression(clip_duration)
            else ""
        ),
        polished=polish,
    )
