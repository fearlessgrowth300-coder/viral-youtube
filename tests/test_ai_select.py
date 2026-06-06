from pathlib import Path
from types import SimpleNamespace

from clip_agent import ai_select
from clip_agent.models import TranscriptSegment


def test_viral_analysis_falls_back_and_caches_without_openai(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ai_select, "VIRAL_ANALYSIS_CACHE_ROOT", tmp_path / "analysis")
    segments = [
        TranscriptSegment(0, 4, "Welcome everyone to the stream"),
        TranscriptSegment(40, 45, "This is absolutely crazy, nobody expected that reaction"),
        TranscriptSegment(90, 95, "I cannot believe the final answer was right"),
    ]
    config = SimpleNamespace(openai_api_key=None, openai_analysis_model="gpt-test")

    first = ai_select.analyze_transcript_for_viral_moments(
        "https://youtu.be/viraltest01",
        segments,
        120,
        config,
        max_moments=3,
    )
    second = ai_select.analyze_transcript_for_viral_moments(
        "https://youtu.be/viraltest01",
        segments,
        120,
        config,
        max_moments=3,
    )

    assert first["provider"] == "local-scoring"
    assert first["moments"]
    assert first["moments"][0]["score"] >= first["moments"][-1]["score"]
    assert second["cached"] is True
    assert ai_select.candidates_from_viral_analysis(second, 120)


def test_analysis_model_candidates_add_compatible_fallbacks() -> None:
    assert ai_select.analysis_model_candidates("gpt-5-mini") == [
        "gpt-5-mini",
        "gpt-4.1-mini",
        "gpt-4o-mini",
    ]
    assert ai_select.analysis_model_candidates("gpt-4.1-mini") == [
        "gpt-4.1-mini",
        "gpt-4o-mini",
    ]


def test_model_access_error_uses_fallback() -> None:
    assert ai_select.should_try_fallback_model(
        RuntimeError("organization must be verified; code=model_not_found")
    )
    assert not ai_select.should_try_fallback_model(RuntimeError("rate limit reached"))
