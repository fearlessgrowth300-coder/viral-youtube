from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and not os.environ.get(key):
            os.environ[key] = value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class AgentConfig:
    openai_api_key: str | None
    openai_analysis_model: str
    openai_transcribe_model: str
    openai_tts_model: str
    openai_tts_voice: str
    allow_youtube_download: bool
    publish_enabled: bool
    require_manual_review: bool
    background_audio_path: Path | None
    background_volume: float
    default_clip_length_seconds: int
    youtube_client_secrets: Path | None
    youtube_token_file: Path
    youtube_privacy_status: str
    youtube_category_id: str
    tiktok_access_token: str | None
    tiktok_privacy_level: str
    instagram_access_token: str | None
    instagram_user_id: str | None
    instagram_api_version: str
    public_media_base_url: str | None

    @classmethod
    def from_env(cls) -> "AgentConfig":
        load_env_file(Path.cwd() / ".env")
        background = os.getenv("BACKGROUND_AUDIO_PATH") or ""
        youtube_secrets = os.getenv("YOUTUBE_CLIENT_SECRETS") or ""
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_analysis_model=os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-5-mini"),
            openai_transcribe_model=os.getenv(
                "OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"
            ),
            openai_tts_model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            openai_tts_voice=os.getenv("OPENAI_TTS_VOICE", "cedar"),
            allow_youtube_download=env_bool("ALLOW_YOUTUBE_DOWNLOAD", False),
            publish_enabled=env_bool("PUBLISH_ENABLED", False),
            require_manual_review=env_bool("REQUIRE_MANUAL_REVIEW", True),
            background_audio_path=Path(background) if background else None,
            background_volume=env_float("BACKGROUND_VOLUME", 0.12),
            default_clip_length_seconds=env_int("CLIP_LENGTH_SECONDS", 45),
            youtube_client_secrets=Path(youtube_secrets) if youtube_secrets else None,
            youtube_token_file=Path(
                os.getenv("YOUTUBE_TOKEN_FILE", ".secrets/youtube_token.json")
            ),
            youtube_privacy_status=os.getenv("YOUTUBE_PRIVACY_STATUS", "private"),
            youtube_category_id=os.getenv("YOUTUBE_CATEGORY_ID", "24"),
            tiktok_access_token=os.getenv("TIKTOK_ACCESS_TOKEN") or None,
            tiktok_privacy_level=os.getenv("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY"),
            instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN") or None,
            instagram_user_id=os.getenv("INSTAGRAM_USER_ID") or None,
            instagram_api_version=os.getenv("INSTAGRAM_API_VERSION", "v23.0"),
            public_media_base_url=os.getenv("PUBLIC_MEDIA_BASE_URL") or None,
        )
