from __future__ import annotations

import json
from dataclasses import asdict

from .config import AgentConfig
from .models import ClipCandidate, TranscriptSegment
from .scoring import build_description, build_hook, build_tags, build_voiceover, classify_kind, is_generic_hook


def refine_candidates_with_openai(
    candidates: list[ClipCandidate],
    segments: list[TranscriptSegment],
    duration: float,
    config: AgentConfig,
) -> list[ClipCandidate]:
    if not config.openai_api_key or not segments:
        return candidates

    transcript = "\n".join(
        f"{segment.start:.1f}-{segment.end:.1f}: {segment.text}" for segment in segments[:500]
    )
    current = json.dumps([asdict(candidate) for candidate in candidates], ensure_ascii=True)
    prompt = (
        "You are a short-form video producer. Pick the strongest clips from the transcript. "
        "Favor funny, surprising, emotional, useful, or high-energy moments. "
        "Return only a JSON array. Each item must have start, end, title, reason, score, "
        "kind, caption, hook, voiceover, description, and tags. The hook is a retention text overlay "
        "that stays on screen for the whole clip. It must use a specific phrase or claim from the "
        "transcript and create urgency. Do not use generic hooks like 'watch this part', "
        "'the hidden part', or 'viral moment'. Keep clips inside the video duration and keep captions short.\n\n"
        f"Video duration seconds: {duration:.1f}\n"
        f"Initial candidates:\n{current}\n\n"
        f"Transcript:\n{transcript}"
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.openai_api_key)
        response = client.responses.create(model=config.openai_analysis_model, input=prompt)
        text = getattr(response, "output_text", "") or ""
        refined = json.loads(text)
    except Exception:
        return candidates

    output: list[ClipCandidate] = []
    for item in refined:
        try:
            start = max(0.0, float(item["start"]))
            end = min(duration or float(item["end"]), float(item["end"]))
            if end <= start:
                continue
            text_for_defaults = str(item.get("reason") or item.get("title") or "Highlight")
            kind = str(item.get("kind") or classify_kind(text_for_defaults))[:40]
            raw_hook = str(item.get("hook") or item.get("caption") or "")
            hook = raw_hook if raw_hook and not is_generic_hook(raw_hook) else build_hook(text_for_defaults, len(output) + 1)
            hook = hook[:86]
            output.append(
                ClipCandidate(
                    start=round(start, 2),
                    end=round(end, 2),
                    title=str(item.get("title") or "Highlight")[:90],
                    reason=str(item.get("reason") or "Selected by OpenAI"),
                    score=float(item.get("score") or 1.0),
                    kind=kind,
                    caption=str(item.get("caption") or hook)[:120],
                    voiceover=str(item.get("voiceover") or build_voiceover(text_for_defaults, hook))[:220],
                    hook=hook,
                    description=str(item.get("description") or build_description(text_for_defaults, hook))[:260],
                    tags=tuple(item.get("tags") or build_tags(text_for_defaults, kind)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return output or candidates
