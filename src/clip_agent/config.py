from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


LOCAL_SETTINGS_PATH = Path.cwd() / ".secrets" / "app_settings.json"
SETTINGS_PIN_PATH = Path.cwd() / ".secrets" / "settings_pin.txt"
SETTING_NAMES = {
    "OPENAI_API_KEY",
    "TRANSCRIPT_API_KEY",
    "YOUTUBE_CLIENT_SECRETS",
    "TIKTOK_ACCESS_TOKEN",
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_USER_ID",
    "PUBLIC_MEDIA_BASE_URL",
    "PUBLISH_ENABLED",
}


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


def load_local_settings(path: Path = LOCAL_SETTINGS_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in payload.items()
        if key in SETTING_NAMES and value is not None
    }


def save_local_settings(
    updates: dict[str, object],
    path: Path = LOCAL_SETTINGS_PATH,
) -> dict[str, str]:
    settings = load_local_settings(path)
    for name, raw_value in updates.items():
        if name not in SETTING_NAMES or raw_value is None:
            continue
        value = str(raw_value).strip()
        if value:
            settings[name] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    temporary.replace(path)
    return settings


def get_or_create_settings_pin(path: Path = SETTINGS_PIN_PATH) -> str:
    if path.exists():
        pin = path.read_text(encoding="utf-8").strip()
        if pin:
            return pin
    path.parent.mkdir(parents=True, exist_ok=True)
    pin = f"{secrets.randbelow(1_000_000):06d}"
    path.write_text(pin, encoding="utf-8")
    return pin


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
    transcript_api_key: str | None = None
    transcript_api_base_url: str = "https://transcriptapi.com/api/v2"
    piper_model_path: Path | None = None
    piper_speaker_id: int = 0

    @classmethod
    def from_env(cls) -> "AgentConfig":
        load_env_file(Path.cwd() / ".env")
        local = load_local_settings()

        def value(name: str, default: str = "") -> str:
            if name in local:
                return local[name]
            return os.getenv(name, default)

        def float_value(name: str, default: float) -> float:
            try:
                return float(value(name, str(default)))
            except ValueError:
                return default

        def int_value(name: str, default: int) -> int:
            try:
                return int(value(name, str(default)))
            except ValueError:
                return default

        background = value("BACKGROUND_AUDIO_PATH")
        youtube_secrets = value("YOUTUBE_CLIENT_SECRETS")
        piper_model = value(
            "PIPER_MODEL_PATH",
            ".tools/piper-voices/en_US-libritts-high.onnx",
        )
        return cls(
            openai_api_key=value("OPENAI_API_KEY") or None,
            openai_analysis_model=value("OPENAI_ANALYSIS_MODEL", "gpt-5-mini"),
            openai_transcribe_model=value(
                "OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"
            ),
            openai_tts_model=value("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            openai_tts_voice=value("OPENAI_TTS_VOICE", "cedar"),
            allow_youtube_download=value("ALLOW_YOUTUBE_DOWNLOAD").strip().lower()
            in {"1", "true", "yes", "y", "on"},
            publish_enabled=value("PUBLISH_ENABLED").strip().lower()
            in {"1", "true", "yes", "y", "on"},
            require_manual_review=value("REQUIRE_MANUAL_REVIEW", "true").strip().lower()
            in {"1", "true", "yes", "y", "on"},
            background_audio_path=Path(background) if background else None,
            background_volume=float_value("BACKGROUND_VOLUME", 0.12),
            default_clip_length_seconds=int_value("CLIP_LENGTH_SECONDS", 45),
            youtube_client_secrets=Path(youtube_secrets) if youtube_secrets else None,
            youtube_token_file=Path(
                value("YOUTUBE_TOKEN_FILE", ".secrets/youtube_token.json")
            ),
            youtube_privacy_status=value("YOUTUBE_PRIVACY_STATUS", "private"),
            youtube_category_id=value("YOUTUBE_CATEGORY_ID", "24"),
            tiktok_access_token=value("TIKTOK_ACCESS_TOKEN") or None,
            tiktok_privacy_level=value("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY"),
            instagram_access_token=value("INSTAGRAM_ACCESS_TOKEN") or None,
            instagram_user_id=value("INSTAGRAM_USER_ID") or None,
            instagram_api_version=value("INSTAGRAM_API_VERSION", "v23.0"),
            public_media_base_url=value("PUBLIC_MEDIA_BASE_URL") or None,
            transcript_api_key=value("TRANSCRIPT_API_KEY") or None,
            transcript_api_base_url=value(
                "TRANSCRIPT_API_BASE_URL", "https://transcriptapi.com/api/v2"
            ),
            piper_model_path=Path(piper_model) if piper_model else None,
            piper_speaker_id=int_value("PIPER_SPEAKER_ID", 0),
        )
