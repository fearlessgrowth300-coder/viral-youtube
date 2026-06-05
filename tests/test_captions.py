from pathlib import Path

from clip_agent.captions import format_srt_time, write_srt
from clip_agent.models import ClipCandidate, TranscriptSegment


def test_format_srt_time() -> None:
    assert format_srt_time(65.432) == "00:01:05,432"


def test_write_srt_uses_relative_clip_timing(tmp_path: Path) -> None:
    candidate = ClipCandidate(
        start=10,
        end=20,
        title="Funny moment",
        reason="test",
        score=1,
    )
    segments = [TranscriptSegment(start=12, end=14, text="this is funny")]
    path = write_srt(tmp_path / "clip.srt", candidate, segments)
    text = path.read_text(encoding="utf-8")
    assert "00:00:02,000 --> 00:00:04,000" in text
    assert "this is funny" in text
