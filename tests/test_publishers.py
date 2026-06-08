from pathlib import Path

from clip_agent.config import AgentConfig
from clip_agent.publishers.base import PublishMetadata, PublishBlocked, require_publish_allowed
from clip_agent.publishers.dispatcher import append_publish_queue
from clip_agent.models import ClipCandidate, RenderedClip


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


def test_publish_requires_global_enable() -> None:
    try:
        require_publish_allowed(minimal_config(), approved=True)
    except PublishBlocked as exc:
        assert "PUBLISH_ENABLED" in str(exc)
    else:
        raise AssertionError("expected PublishBlocked")


def test_append_publish_queue(tmp_path: Path) -> None:
    candidate = ClipCandidate(start=0, end=10, title="Clip", reason="because", score=1)
    clip = RenderedClip(
        video_path=tmp_path / "clip.mp4",
        srt_path=tmp_path / "clip.srt",
        metadata_path=tmp_path / "clip.json",
        candidate=candidate,
        thumbnail_path=tmp_path / "clip-thumbnail.jpg",
    )
    queue = tmp_path / "publish_queue.jsonl"
    append_publish_queue(queue, [clip], ["youtube", "tiktok"])
    text = queue.read_text(encoding="utf-8")
    assert '"platform": "youtube"' in text
    assert '"platform": "tiktok"' in text
    assert '"thumbnail_path":' in text


def test_append_publish_queue_skips_empty_clip_list(tmp_path: Path) -> None:
    queue = tmp_path / "publish_queue.jsonl"
    append_publish_queue(queue, [], ["youtube"])
    assert not queue.exists()
