from __future__ import annotations

from pathlib import Path
import re
import subprocess
import wave
from typing import Any

from .config import AgentConfig
from .models import ClipCandidate
from .paths import PROJECT_ROOT


_PIPER_VOICES: dict[str, Any] = {}
PIPER_ATTRIBUTION = (
    "Voice generated locally with Piper (GPL-3.0-or-later) using the "
    "en_US-libritts-high model trained on LibriTTS (CC BY 4.0)."
)


def generate_voiceover(
    candidate: ClipCandidate,
    destination: Path,
    config: AgentConfig,
    provider: str = "local-piper",
    narration_style: str = "movie-recap",
) -> Path | None:
    generated, _ = generate_voiceover_with_provider(
        candidate,
        destination,
        config,
        provider=provider,
        narration_style=narration_style,
    )
    return generated


def generate_voiceover_with_provider(
    candidate: ClipCandidate,
    destination: Path,
    config: AgentConfig,
    provider: str = "local-piper",
    narration_style: str = "movie-recap",
) -> tuple[Path | None, str]:
    text = narration_text(candidate)
    if not text:
        return None, ""
    destination.parent.mkdir(parents=True, exist_ok=True)
    provider = str(provider or "local-piper").strip().lower()

    if provider in {"local-piper", "piper"}:
        generated = generate_piper_voiceover(
            text,
            destination.with_suffix(".wav"),
            config,
            narration_style,
        )
        if generated:
            return generated, "Piper local neural voice"
        generated = generate_windows_voiceover(
            text,
            destination.with_suffix(".wav"),
            narration_style=narration_style,
        )
        return generated, "Windows offline voice" if generated else ""

    if provider == "windows":
        generated = generate_windows_voiceover(
            text,
            destination.with_suffix(".wav"),
            narration_style=narration_style,
        )
        return generated, "Windows offline voice" if generated else ""

    if provider == "openai" and config.openai_api_key:
        generated = generate_openai_voiceover(
            text,
            destination,
            config,
            narration_style,
        )
        if generated:
            return generated, "OpenAI voice"

    generated = generate_piper_voiceover(
        text,
        destination.with_suffix(".wav"),
        config,
        narration_style,
    )
    if generated:
        return generated, "Piper local neural voice"
    generated = generate_windows_voiceover(
        text,
        destination.with_suffix(".wav"),
        narration_style=narration_style,
    )
    return generated, "Windows offline voice" if generated else ""


def narration_text(candidate: ClipCandidate) -> str:
    parts = [
        candidate.voiceover,
        candidate.hook,
        candidate.description,
    ]
    clean_parts: list[str] = []
    seen: set[str] = set()
    for part in parts:
        clean = re.sub(r"\s+", " ", str(part or "")).strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        clean_parts.append(clean)
    return " ".join(clean_parts)[:900]


def narration_instructions(style: str) -> str:
    if style == "movie-recap":
        return (
            "Speak like a polished movie recap narrator: controlled, dramatic, clear, "
            "and slightly urgent without shouting."
        )
    if style == "energetic":
        return "Speak like an energetic social video narrator with crisp pacing."
    return "Speak naturally, clearly, and conversationally."


def generate_openai_voiceover(
    text: str,
    destination: Path,
    config: AgentConfig,
    narration_style: str,
) -> Path | None:
    try:
        from openai import OpenAI
    except ImportError:
        return None
    client = OpenAI(api_key=config.openai_api_key)
    try:
        with client.audio.speech.with_streaming_response.create(
            model=config.openai_tts_model,
            voice=config.openai_tts_voice,
            input=text,
            instructions=narration_instructions(narration_style),
        ) as response:
            response.stream_to_file(destination)
        return destination
    except Exception:
        return None


def resolve_piper_model(config: AgentConfig) -> Path | None:
    model = config.piper_model_path
    if not model:
        return None
    if not model.is_absolute():
        model = PROJECT_ROOT / model
    return model.resolve() if model.exists() else None


def generate_piper_voiceover(
    text: str,
    destination: Path,
    config: AgentConfig,
    narration_style: str = "movie-recap",
) -> Path | None:
    model = resolve_piper_model(config)
    if not model or not model.with_suffix(model.suffix + ".json").exists():
        return None
    try:
        from piper import PiperVoice, SynthesisConfig
    except ImportError:
        return None

    try:
        voice = _PIPER_VOICES.get(str(model))
        if voice is None:
            voice = PiperVoice.load(model)
            _PIPER_VOICES[str(model)] = voice
        length_scale = {
            "movie-recap": 0.94,
            "energetic": 0.88,
            "natural": 1.0,
        }.get(narration_style, 0.94)
        with wave.open(str(destination), "wb") as wav_file:
            voice.synthesize_wav(
                text[:900],
                wav_file,
                SynthesisConfig(
                    speaker_id=max(0, int(config.piper_speaker_id)),
                    length_scale=length_scale,
                    volume=1.05,
                ),
            )
        if destination.exists() and destination.stat().st_size > 1024:
            return destination
    except Exception:
        if destination.exists():
            destination.unlink()
    return None


def generate_windows_voiceover(
    text: str,
    destination: Path,
    narration_style: str = "movie-recap",
) -> Path | None:
    voice_name = (
        "Microsoft David Desktop"
        if narration_style == "movie-recap"
        else "Microsoft Zira Desktop"
    )
    rate = 0 if narration_style == "movie-recap" else 1
    script = (
        "param($voicePath, $voiceText, $voiceName, $voiceRate); "
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "try { $synth.SelectVoice($voiceName) } catch {}; "
        "$synth.Rate = [int]$voiceRate; $synth.Volume = 100; "
        "$synth.SetOutputToWaveFile($voicePath); "
        "$synth.Speak($voiceText); "
        "$synth.Dispose();"
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& {{ {script} }}",
            str(destination),
            text[:260],
            voice_name,
            str(rate),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not destination.exists():
        return None
    return destination
