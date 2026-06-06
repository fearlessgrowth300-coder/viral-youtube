import json
from pathlib import Path
from types import SimpleNamespace

import requests

from clip_agent.transcribe import (
    TranscriptAPICreditsExhausted,
    fetch_transcript_api,
    find_sidecar_transcript,
    load_transcript_file,
    load_vtt,
)


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


def response(status: int, payload: dict) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode("utf-8")
    result.headers["Content-Type"] = "application/json"
    return result


def test_fetch_transcript_api_returns_timestamped_segments(tmp_path: Path, monkeypatch) -> None:
    def fake_get(url, params, headers, timeout):
        assert url.endswith("/youtube/transcript")
        assert params["video_url"].startswith("https://youtu.be/")
        assert headers["Authorization"] == "Bearer test-key"
        return response(
            200,
            {
                "video_id": "abcdefghijk",
                "language": "en",
                "transcript": [
                    {"text": "This gets wild", "start": 4.0, "duration": 2.5},
                ],
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)
    config = SimpleNamespace(
        transcript_api_key="test-key",
        transcript_api_base_url="https://transcriptapi.com/api/v2",
    )
    segments = fetch_transcript_api(
        "https://youtu.be/abcdefghijk",
        tmp_path,
        config,
        cache_root=tmp_path / "cache",
    )
    assert segments[0].start == 4.0
    assert segments[0].end == 6.5
    assert segments[0].text == "This gets wild"
    assert (tmp_path / "transcriptapi.json").exists()


def test_fetch_transcript_api_reports_exhausted_credits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        requests,
        "get",
        lambda *args, **kwargs: response(
            402,
            {"detail": {"message": "No credits", "reason": "insufficient_credits"}},
        ),
    )
    config = SimpleNamespace(
        transcript_api_key="test-key",
        transcript_api_base_url="https://transcriptapi.com/api/v2",
    )
    try:
        fetch_transcript_api(
            "https://youtu.be/abcdefghijk",
            tmp_path,
            config,
            cache_root=tmp_path / "cache",
        )
    except TranscriptAPICreditsExhausted as exc:
        assert "credits are exhausted" in str(exc)
    else:
        raise AssertionError("expected TranscriptAPICreditsExhausted")


def test_fetch_transcript_api_reuses_global_cache(tmp_path: Path, monkeypatch) -> None:
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return response(
            200,
            {
                "video_id": "cachevideo1",
                "language": "en",
                "transcript": [
                    {"text": "The crowd went wild", "start": 12.0, "duration": 3.0},
                ],
            },
        )

    monkeypatch.setattr(requests, "get", fake_get)
    config = SimpleNamespace(
        transcript_api_key="test-key",
        transcript_api_base_url="https://transcriptapi.com/api/v2",
    )
    cache_root = tmp_path / "shared-cache"
    first = fetch_transcript_api(
        "https://youtu.be/cachevideo1",
        tmp_path / "first",
        config,
        cache_root=cache_root,
    )
    second = fetch_transcript_api(
        "https://youtu.be/cachevideo1",
        tmp_path / "second",
        config,
        cache_root=cache_root,
    )
    assert calls == 1
    assert second == first
