from pathlib import Path
from types import SimpleNamespace

from clip_agent.config import AgentConfig
from clip_agent.models import ClipCandidate
from clip_agent.voiceover import generate_elevenlabs_voiceover, generate_voiceover


def minimal_config() -> AgentConfig:
    return AgentConfig(
        openai_api_key="test-key",
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


def test_openai_voiceover_error_falls_back_without_raising(monkeypatch, tmp_path: Path) -> None:
    class BrokenSpeech:
        def create(self, **kwargs):
            raise RuntimeError("Connection error.")

    class FakeOpenAI:
        def __init__(self, api_key: str):
            self.audio = SimpleNamespace(
                speech=SimpleNamespace(with_streaming_response=BrokenSpeech())
            )

    monkeypatch.setitem(__import__("sys").modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(
        "clip_agent.voiceover.generate_piper_voiceover",
        lambda text, destination, config, narration_style: None,
    )
    monkeypatch.setattr(
        "clip_agent.voiceover.generate_windows_voiceover",
        lambda text, destination, narration_style="movie-recap": None,
    )
    candidate = ClipCandidate(
        start=0,
        end=5,
        title="Clip",
        reason="test",
        score=1,
        voiceover="Say this",
    )
    assert (
        generate_voiceover(
            candidate,
            tmp_path / "voice.mp3",
            minimal_config(),
            provider="openai",
        )
        is None
    )


def test_elevenlabs_voiceover_writes_streamed_audio(monkeypatch, tmp_path: Path) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            return iter([b"a" * 700, b"b" * 700])

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("clip_agent.voiceover.requests.post", fake_post)
    config = minimal_config()
    config = AgentConfig(
        **{
            **config.__dict__,
            "elevenlabs_api_key": "eleven-key",
            "elevenlabs_voice_id": "voice-123",
        }
    )
    destination = tmp_path / "voice.mp3"
    generated = generate_elevenlabs_voiceover(
        "Movie recap narration",
        destination,
        config,
        "movie-recap",
    )
    assert generated == destination
    assert destination.stat().st_size == 1400
    assert captured["headers"]["xi-api-key"] == "eleven-key"
    assert captured["json"]["model_id"] == "eleven_multilingual_v2"
    assert captured["url"].endswith("/voice-123")
