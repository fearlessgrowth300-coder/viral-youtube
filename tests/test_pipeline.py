from clip_agent.models import ClipCandidate
from clip_agent.pipeline import clamp_candidate_length


def test_clamp_candidate_length_keeps_ai_refinement_inside_requested_clip_length() -> None:
    candidate = ClipCandidate(
        start=100,
        end=208,
        title="Long AI clip",
        reason="test",
        score=1,
    )
    clamped = clamp_candidate_length(candidate, max_seconds=45, duration=300)
    assert clamped.start == 100
    assert clamped.end == 145
