from pathlib import Path

from clip_agent.transcribe import find_sidecar_transcript, load_transcript_file, load_vtt


def test_load_vtt_parses_and_cleans_caption_text(tmp_path: Path) -> None:
    path = tmp_path / "sample.en.vtt"
    path.write_text(
        """WEBVTT

00:00:01.000 --> 00:00:04.000 align:start position:0%
<v Speaker><00:00:01.500>This is <c>funny</c>

00:00:05.500 --> 00:00:07.000
That was wild!
""",
        encoding="utf-8",
    )
    segments = load_vtt(path)
    assert len(segments) == 2
    assert segments[0].start == 1.0
    assert segments[0].text == "This is funny"
    assert segments[1].text == "That was wild!"


def test_find_sidecar_transcript_prefers_matching_srt(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    srt = tmp_path / "video.en.srt"
    video.write_text("", encoding="utf-8")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    assert find_sidecar_transcript(str(video)) == srt
    assert load_transcript_file(srt)[0].text == "hello"
