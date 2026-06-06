from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import AgentConfig
from .models import ClipCandidate, TranscriptSegment
from .scoring import (
    build_description,
    build_engagement_question,
    build_hook,
    build_tags,
    build_voiceover,
    classify_kind,
    is_generic_hook,
    select_candidates,
)
from .transcribe import youtube_cache_key


VIRAL_ANALYSIS_CACHE_ROOT = Path.cwd() / ".cache" / "viral-analysis"


def extract_json_response(text: str) -> Any:
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1] if "\n" in clean else clean
        clean = clean.rsplit("```", 1)[0].strip()
    return json.loads(clean)


def transcript_fingerprint(segments: list[TranscriptSegment]) -> str:
    digest = hashlib.sha1()
    for segment in segments:
        digest.update(f"{segment.start:.2f}|{segment.end:.2f}|{segment.text}\n".encode("utf-8"))
    return digest.hexdigest()


def viral_analysis_cache_path(source_url: str) -> Path:
    return VIRAL_ANALYSIS_CACHE_ROOT / f"{youtube_cache_key(source_url)}.json"


def load_cached_viral_analysis(
    source_url: str,
    segments: list[TranscriptSegment] | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    path = viral_analysis_cache_path(source_url)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if segments and payload.get("transcript_fingerprint") != transcript_fingerprint(segments):
        return None
    cached_request_model = payload.get("requested_model") or payload.get("model")
    if model and cached_request_model != model:
        return None
    return payload


def analysis_model_candidates(configured_model: str) -> list[str]:
    return list(
        dict.fromkeys(
            model
            for model in (configured_model, "gpt-4.1-mini", "gpt-4o-mini")
            if model
        )
    )


def should_try_fallback_model(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "model_not_found",
            "must be verified",
            "does not exist",
            "do not have access",
            "not have access",
        )
    )


def transcript_prompt_text(
    segments: list[TranscriptSegment],
    max_characters: int = 90_000,
) -> str:
    lines = [
        f"{segment.start:.2f}-{segment.end:.2f}: {segment.text}"
        for segment in segments
    ]
    if sum(len(line) + 1 for line in lines) <= max_characters:
        return "\n".join(lines)
    step = max(1, len(lines) // 900)
    sampled = lines[::step]
    return "\n".join(sampled)[:max_characters]


def candidate_from_analysis_item(
    item: dict[str, Any],
    rank: int,
    duration: float,
) -> ClipCandidate | None:
    try:
        start = max(0.0, float(item.get("start") or 0.0))
        end = float(item.get("end") or (start + 45.0))
        score = float(item.get("score") or max(1.0, 100.0 - rank))
    except (TypeError, ValueError):
        return None
    end = min(duration or end, end)
    if end <= start:
        return None
    evidence = str(
        item.get("transcript_quote")
        or item.get("reason")
        or item.get("title")
        or "Highlight"
    )
    kind = str(item.get("kind") or classify_kind(evidence))[:40]
    hook = str(item.get("hook") or build_hook(evidence, rank))[:86]
    if is_generic_hook(hook):
        hook = build_hook(evidence, rank)
    engagement_question = str(
        item.get("engagement_question")
        or build_engagement_question(evidence, kind)
    )[:100]
    tags = item.get("tags") or build_tags(evidence, kind)
    if isinstance(tags, str):
        tags = [tag.strip().lstrip("#") for tag in tags.split(",")]
    return ClipCandidate(
        start=round(start, 2),
        end=round(end, 2),
        title=str(item.get("title") or "Viral Highlight")[:100],
        reason=str(item.get("reason") or f"Transcript evidence: {evidence}")[:400],
        score=max(0.0, min(100.0, score)),
        kind=kind,
        caption=str(item.get("caption") or hook)[:120],
        voiceover=str(item.get("voiceover") or build_voiceover(evidence, hook))[:220],
        hook=hook,
        description=str(
            item.get("description")
            or build_description(evidence, hook, engagement_question)
        )[:500],
        tags=tuple(str(tag).strip().lstrip("#") for tag in tags if str(tag).strip()),
        engagement_question=engagement_question,
    )


def candidates_from_viral_analysis(
    payload: dict[str, Any],
    duration: float,
) -> list[ClipCandidate]:
    candidates = [
        candidate_from_analysis_item(item, rank, duration)
        for rank, item in enumerate(payload.get("moments") or [], start=1)
        if isinstance(item, dict)
    ]
    return [candidate for candidate in candidates if candidate is not None]


def fallback_viral_analysis(
    source_url: str,
    segments: list[TranscriptSegment],
    duration: float,
    max_moments: int,
    error: str = "",
) -> dict[str, Any]:
    candidates = sorted(
        select_candidates(segments, duration, max_moments, 45),
        key=lambda candidate: candidate.score,
        reverse=True,
    )
    return {
        "source": source_url,
        "provider": "local-scoring",
        "model": "local-scoring",
        "transcript_fingerprint": transcript_fingerprint(segments),
        "transcript_segments": len(segments),
        "summary": "Ranked from transcript language, urgency, surprise, emotion, and reaction signals.",
        "analysis_error": error,
        "moments": [
            {
                **asdict(candidate),
                "transcript_quote": candidate.reason.split(":", 1)[-1].strip(),
            }
            for candidate in candidates
        ],
    }


def analyze_transcript_for_viral_moments(
    source_url: str,
    segments: list[TranscriptSegment],
    duration: float,
    config: AgentConfig,
    max_moments: int = 10,
) -> dict[str, Any]:
    requested_model = config.openai_analysis_model if config.openai_api_key else None
    cached = load_cached_viral_analysis(source_url, segments, requested_model)
    if cached:
        return {**cached, "cached": True}
    if not segments:
        return {
            "source": source_url,
            "provider": "none",
            "transcript_segments": 0,
            "summary": "",
            "moments": [],
        }
    if not config.openai_api_key:
        payload = fallback_viral_analysis(source_url, segments, duration, max_moments)
    else:
        prompt = (
            "You are an elite YouTube Shorts and reaction-video editor. Analyze the timestamped "
            "transcript and identify the strongest moments that can make viewers stop scrolling. "
            f"Return ONLY a JSON object with summary and moments, with at most {max_moments} moments. "
            "Every moment must contain start, end, score (0-100), kind, title, hook, description, "
            "tags, engagement_question, reason, and transcript_quote. Rank moments strongest first. "
            "Choose funny, crazy, shocking, emotional, controversial, surprising, high-energy, or "
            "deeply useful payoffs, not consecutive sections from the beginning. Start no more than "
            "3 seconds before the payoff. Each normal Short should be 20-60 seconds. The hook must be "
            "a bold claim or challenge grounded in the transcript and designed for the first 5 seconds. "
            "The title must be specific and clickable without lying. The description must explain the "
            "actual moment and end with the engagement question. Tags must be relevant, lowercase, "
            "and contain no generic spam. transcript_quote must copy the exact evidence from the transcript. "
            "Do not invent people, events, quotes, or claims that are absent from the transcript.\n\n"
            f"Video duration: {duration:.2f} seconds\n"
            f"Transcript:\n{transcript_prompt_text(segments)}"
        )
        try:
            from openai import OpenAI

            client = OpenAI(api_key=config.openai_api_key)
            response = None
            selected_model = ""
            last_model_error: Exception | None = None
            for model in analysis_model_candidates(config.openai_analysis_model):
                try:
                    response = client.responses.create(model=model, input=prompt)
                    selected_model = model
                    break
                except Exception as exc:
                    last_model_error = exc
                    if not should_try_fallback_model(exc):
                        raise
            if response is None:
                raise last_model_error or RuntimeError("No OpenAI analysis model was available.")
            parsed = extract_json_response(getattr(response, "output_text", "") or "")
            raw_moments = parsed.get("moments") if isinstance(parsed, dict) else []
            candidate_items = []
            for rank, item in enumerate(raw_moments or [], start=1):
                if not isinstance(item, dict):
                    continue
                candidate = candidate_from_analysis_item(item, rank, duration)
                if candidate:
                    candidate_items.append((candidate, item))
            if not candidate_items:
                raise ValueError("OpenAI returned no usable viral moments.")
            payload = {
                "source": source_url,
                "provider": "openai",
                "model": selected_model,
                "requested_model": config.openai_analysis_model,
                "transcript_fingerprint": transcript_fingerprint(segments),
                "transcript_segments": len(segments),
                "summary": str(parsed.get("summary") or "OpenAI ranked the strongest transcript moments."),
                "moments": [
                    {
                        **asdict(candidate),
                        "transcript_quote": str(item.get("transcript_quote") or ""),
                    }
                    for candidate, item in candidate_items
                ],
            }
        except Exception as exc:
            payload = fallback_viral_analysis(
                source_url,
                segments,
                duration,
                max_moments,
                error=f"OpenAI fallback used: {exc}",
            )
    VIRAL_ANALYSIS_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    viral_analysis_cache_path(source_url).write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    return payload


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
        "kind, caption, hook, voiceover, description, tags, and engagement_question. "
        "Start each clip no more than 3 seconds before the strongest reaction or payoff. "
        "The hook is a bold claim or challenge shown during the first 5 seconds. "
        "It must use a specific phrase or claim from the transcript and create urgency. "
        "The engagement_question should invite a short comment. Do not use generic hooks like 'watch this part', "
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
            engagement_question = str(
                item.get("engagement_question")
                or build_engagement_question(text_for_defaults, kind)
            )[:100]
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
                    description=str(
                        item.get("description")
                        or build_description(text_for_defaults, hook, engagement_question)
                    )[:320],
                    tags=tuple(item.get("tags") or build_tags(text_for_defaults, kind)),
                    engagement_question=engagement_question,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return output or candidates
