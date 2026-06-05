from pathlib import Path
from types import SimpleNamespace

from clip_agent.config import AgentConfig
from clip_agent.pipeline import source_slug
from clip_agent.source import (
    SourcePermissionError,
    analyze_source,
    clean_youtube_error,
    extract_youtube_id,
    is_blocking_youtube_error,
    is_live_info,
    is_youtube_live_url,
    json3_to_vtt,
    prepare_source,
    should_capture_youtube_source,
    youtube_download_options,
)


def minimal_config() -> AgentConfig:
    return AgentConfig(
        openai_api_key=None,
        openai_analysis_model="gpt-5-mini",
        openai_transcribe_model="gpt-4o-mini-transcribe",
        openai_tts_model="gpt-4o-mini-tts",
        openai_tts_voice="cedar",
        allow_youtube_download=False,
        publish_enabled=False,
        require_manual_review=True,
        background_audio_path=None,
        background_volume=0.12,
        default_clip_length_seconds=45,
        youtube_client_secrets=None,
        youtube_token_file=Path(".secrets/youtube.json"),
        youtube_privacy_status="private",
        youtube_category_id="24",
        tiktok_access_token=None,
        tiktok_privacy_level="SELF_ONLY",
        instagram_access_token=None,
        instagram_user_id=None,
        instagram_api_version="v23.0",
        public_media_base_url=None,
    )


def test_source_slug_removes_windows_invalid_url_characters() -> None:
    slug = source_slug("https://youtu.be/kApsw7gsALs?si=M6TAGwcY6T5-RyHm")
    assert "?" not in slug
    assert ":" not in slug
    assert slug == "youtu.be-kApsw7gsALs"


def test_extract_youtube_id_from_common_urls() -> None:
    assert extract_youtube_id("https://youtu.be/kApsw7gsALs?si=x") == "kApsw7gsALs"
    assert extract_youtube_id("https://www.youtube.com/watch?v=abc123") == "abc123"
    assert extract_youtube_id("https://www.youtube.com/live/18leU88yGEU?si=x") == "18leU88yGEU"


def test_prepare_source_checks_url_before_path_exists() -> None:
    try:
        prepare_source(
            "https://youtu.be/kApsw7gsALs?si=M6TAGwcY6T5-RyHm",
            Path("runs"),
            minimal_config(),
        )
    except SourcePermissionError as exc:
        assert "ALLOW_YOUTUBE_DOWNLOAD=true" in str(exc)
    else:
        raise AssertionError("expected SourcePermissionError")


def test_youtube_download_options_include_ffmpeg_location(tmp_path: Path) -> None:
    options = youtube_download_options(tmp_path)
    assert "ffmpeg" in Path(options["ffmpeg_location"]).name.lower()
    assert options["merge_output_format"] == "mp4"
    assert options["no_color"] is True
    assert options["writeautomaticsub"] is True
    assert options["socket_timeout"] == 30
    assert options["retries"] == 3
    assert "height<=1080" in options["format"]


def test_youtube_live_url_uses_capture_mode_even_for_replay() -> None:
    source = "https://www.youtube.com/live/7REUA9nQWIA?si=x"
    assert is_youtube_live_url(source) is True
    assert should_capture_youtube_source(source, {"live_status": "was_live"}) is True
    assert should_capture_youtube_source("https://www.youtube.com/watch?v=abc123", {"live_status": "was_live"}) is False


def test_is_live_info_only_current_live() -> None:
    assert is_live_info({"is_live": True}) is True
    assert is_live_info({"live_status": "is_live"}) is True
    assert is_live_info({"live_status": "was_live"}) is False
    assert is_live_info({"live_status": "not_live"}) is False


def test_json3_to_vtt_converts_caption_events() -> None:
    text = json3_to_vtt(
        '{"events":[{"tStartMs":1000,"dDurationMs":1500,"segs":[{"utf8":"hello "},{"utf8":"live"}]}]}'
    )
    assert text.startswith("WEBVTT")
    assert "00:00:01.000 --> 00:00:02.500" in text
    assert "hello live" in text


def test_clean_youtube_error_removes_terminal_codes() -> None:
    assert clean_youtube_error("\x1b[0;31mERROR:\x1b[0m Video unavailable") == "ERROR: Video unavailable"


def test_is_blocking_youtube_error_detects_unavailable_video() -> None:
    assert is_blocking_youtube_error("ERROR: [youtube] abc: This video is not available")
    assert is_blocking_youtube_error("Private video")
    assert not is_blocking_youtube_error("Temporary metadata lookup failed")


def test_analyze_source_returns_fallback_when_youtube_metadata_fails(monkeypatch) -> None:
    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def extract_info(self, source, download=False):
            raise RuntimeError("This video is not available")

    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    analysis = analyze_source("https://www.youtube.com/live/18leU88yGEU?si=x")
    assert analysis["is_youtube"] is True
    assert analysis["is_live"] is True
    assert analysis["analysis_error"] == "This video is not available"
    assert analysis["thumbnail"].endswith("/18leU88yGEU/hqdefault.jpg")
