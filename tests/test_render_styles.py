from pathlib import Path

from clip_agent.models import ClipCandidate
from clip_agent.render import (
    escape_drawtext,
    hook_filter,
    parse_ffmpeg_progress_seconds,
    render_dimensions,
    render_quality_settings,
    video_filter,
)


def candidate() -> ClipCandidate:
    return ClipCandidate(
        start=0,
        end=10,
        title="Inside Africa's Richest Refinery",
        reason="test",
        score=1.0,
        hook="Inside Africa's Richest Refinery",
        caption="Inside Africa's Richest Refinery",
    )


def test_hook_filter_uses_white_box() -> None:
    rendered = hook_filter(candidate())
    assert "fontcolor=black" in rendered
    assert "boxcolor=white" in rendered
    assert "fontsize=50" in rendered
    assert "text_align=center" in rendered
    assert "between(t,0,5)" not in rendered


def test_hook_filter_rebuilds_generic_hook_from_reason() -> None:
    clip = candidate()
    generic = ClipCandidate(
        start=clip.start,
        end=clip.end,
        title=clip.title,
        reason="Viral score 5.0: A project so massive that people talk about it every day.",
        score=clip.score,
        hook="THE HIDDEN PART",
        caption="THE HIDDEN PART",
    )
    rendered = hook_filter(generic)
    assert "THE HIDDEN PART" not in rendered
    assert "MASSIVE" in rendered


def test_caption_style_can_disable_subtitles() -> None:
    rendered = video_filter(Path("clip.srt"), True, candidate(), True, caption_style="none")
    assert "subtitles=" not in rendered
    assert "drawtext=" in rendered


def test_white_box_caption_style_uses_ass_box() -> None:
    rendered = video_filter(Path("clip.srt"), True, candidate(), False, caption_style="white-box")
    assert "BorderStyle=3" in rendered
    assert "PrimaryColour=&H00000000" in rendered


def test_green_caption_presets_match_picker() -> None:
    karaoke = video_filter(Path("clip.srt"), True, candidate(), False, caption_style="karaoke")
    mozi = video_filter(Path("clip.srt"), True, candidate(), False, caption_style="mozi")
    assert "PrimaryColour=&H0000FF00" in karaoke
    assert "PrimaryColour=&H0000FF00" in mozi


def test_4k_quality_uses_large_lanczos_scaled_filter() -> None:
    rendered = video_filter(Path("clip.srt"), True, candidate(), True, caption_style="none", render_quality="4k")
    assert "scale=2160:3840" in rendered
    assert "crop=2160:3840" in rendered
    assert "flags=lanczos" in rendered
    assert "unsharp=" in rendered
    assert "fontsize=100" in rendered


def test_render_quality_settings() -> None:
    assert render_dimensions(True, "4k") == (2160, 3840)
    assert render_dimensions(False, "2k") == (2560, 1440)
    assert render_quality_settings("4k")["crf"] == "18"


def test_parse_ffmpeg_progress_seconds() -> None:
    assert parse_ffmpeg_progress_seconds("out_time_ms=2500000") == 2.5
    assert parse_ffmpeg_progress_seconds("out_time=00:01:02.500") == 62.5
    assert parse_ffmpeg_progress_seconds("frame=10") is None


def test_escape_drawtext_removes_apostrophes_that_break_ffmpeg() -> None:
    escaped = escape_drawtext("I'M FROM PALESTINE")
    assert "'" not in escaped
    assert "IM FROM PALESTINE" in escaped
    assert "\\n" not in escape_drawtext("A\nB")
