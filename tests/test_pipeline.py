from clip_agent.models import ClipCandidate
from clip_agent.pipeline import clamp_candidate_length, prepare_long_form_candidate


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


def test_prepare_long_form_candidate_uses_horizontal_metadata_tags() -> None:
    candidate = ClipCandidate(
        start=90,
        end=135,
        title="Big Reaction",
        reason="test",
        score=2,
        tags=("shorts", "viral", "bestmoments"),
    )
    long_form = prepare_long_form_candidate(candidate, requested_seconds=600, duration=1200)
    assert long_form.start == 90
    assert long_form.end == 690
    assert "Extended Highlight" in long_form.title
    assert "shorts" not in long_form.tags
    assert "longform" in long_form.tags


def test_prepare_long_form_candidate_uses_whole_shorter_source() -> None:
    candidate = ClipCandidate(start=30, end=60, title="Moment", reason="test", score=1)
    long_form = prepare_long_form_candidate(candidate, requested_seconds=600, duration=300)
    assert long_form.start == 0
    assert long_form.end == 300
