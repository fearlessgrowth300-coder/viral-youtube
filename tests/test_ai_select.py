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
