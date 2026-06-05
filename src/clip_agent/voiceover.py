from __future__ import annotations

from pathlib import Path
import subprocess

from .config import AgentConfig
from .models import ClipCandidate


def generate_voiceover(
    candidate: ClipCandidate, destination: Path, config: AgentConfig
) -> Path | None:
    text = candidate.voiceover.strip()
    if not text:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not config.openai_api_key:
        return generate_windows_voiceover(text, destination.with_suffix(".wav"))

    try:
        from openai import OpenAI
    except ImportError:
        return generate_windows_voiceover(text, destination.with_suffix(".wav"))

    client = OpenAI(api_key=config.openai_api_key)
    try:
        with client.audio.speech.with_streaming_response.create(
            model=config.openai_tts_model,
            voice=config.openai_tts_voice,
            input=text,
            instructions="Speak like a concise social video narrator. Keep the delivery energetic but natural.",
        ) as response:
            response.stream_to_file(destination)
        return destination
    except Exception:
        return generate_windows_voiceover(text, destination.with_suffix(".wav"))


def generate_windows_voiceover(text: str, destination: Path) -> Path | None:
    script = (
        "param($voicePath, $voiceText); "
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$synth.Rate = 1; $synth.Volume = 100; "
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
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not destination.exists():
        return None
    return destination
