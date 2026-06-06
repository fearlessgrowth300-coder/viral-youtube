from clip_agent.models import ClipCandidate
from clip_agent.seo import optimize_candidate


def test_seo_optimizer_preserves_title_and_adds_score_variants_and_tags() -> None:
    candidate = ClipCandidate(
        start=10,
        end=45,
        title="Official FIFA World Cup Album Confirmation",
        reason="The streamer reacts live to the announcement.",
        score=98,
        hook="My song made the official FIFA World Cup album!",
        description="The official announcement triggers a huge live reaction.",
        tags=("fifa", "worldcup", "music"),
        engagement_question="How would you react?",
    )

    optimized = optimize_candidate(candidate, "short")

    assert optimized.title == candidate.title
    assert optimized.tags[:3] == candidate.tags
    assert "shorts" in optimized.tags
    assert optimized.seo_score >= 70
    assert optimized.title_variants
    assert optimized.seo_keywords
