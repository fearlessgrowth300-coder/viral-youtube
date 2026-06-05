from clip_agent.models import TranscriptSegment
from clip_agent.scoring import (
    build_description,
    build_title,
    clean_social_text,
    fallback_candidates,
    is_generic_hook,
    score_text,
    select_candidates,
    build_hook,
)


def test_funny_keywords_score_higher() -> None:
    assert score_text("that was hilarious wow!") > score_text("today we started the meeting")


def test_select_candidates_limits_and_orders() -> None:
    segments = [
        TranscriptSegment(start=0, end=4, text="normal intro"),
        TranscriptSegment(start=60, end=65, text="wait this is a crazy funny moment!"),
        TranscriptSegment(start=180, end=185, text="another best moment wow"),
    ]
    clips = select_candidates(segments, duration=240, max_clips=2, clip_length=30)
    assert len(clips) == 2
    assert clips[0].start < clips[1].start
    assert any("funny" in clip.reason.lower() for clip in clips)


def test_fallback_candidates_do_not_use_generic_viral_moment_text() -> None:
    clips = fallback_candidates(duration=180, max_clips=2, clip_length=30)
    text = " ".join(f"{clip.title} {clip.caption} {clip.hook} {clip.voiceover}" for clip in clips)
    assert "Viral Moment" not in text
    assert "VIRAL MOMENT" not in text
    assert clips[0].start > 0


def test_genre_bias_changes_score() -> None:
    text = "This billion dollar energy project changed the whole industry scale"
    assert score_text(text, genre="business") > score_text(text, genre="funny")


def test_build_hook_uses_transcript_phrase_not_generic_hidden_part() -> None:
    hook = build_hook(
        "Many people hear about this place, the Dangote Refinery. "
        "A project so massive that people talk about it every day on the internet.",
        1,
    )
    assert not is_generic_hook(hook)
    assert "MASSIVE" in hook or "DANGOTE" in hook


def test_social_text_cleanup_removes_repeated_transcript_words() -> None:
    raw = "you sun sun sun with with with night night night away like you in the daylight"
    assert clean_social_text(raw) == "you sun with night away like you in the daylight"
    title = build_title(raw, 1)
    description = build_description(raw, "KEEP WATCHING")
    assert "sun sun" not in title.lower()
    assert "with with" not in description.lower()
