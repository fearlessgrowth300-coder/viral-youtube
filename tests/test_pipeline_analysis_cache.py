from pathlib import Path
from types import SimpleNamespace

from clip_agent.models import RenderedClip, TranscriptSegment
from clip_agent.pipeline import RunOptions, run_once


def test_run_reuses_exact_paste_time_openai_metadata(tmp_path: Path, monkeypatch) -> None:
    source_url = "https://www.youtube.com/live/7REUA9nQWIA?si=test"
    cached_analysis = {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "requested_model": "gpt-5-mini",
        "transcript_duration": 6268.0,
        "moments": [
            {
                "start": 718,
                "end": 749,
                "score": 98,
                "kind": "surprise",
                "title": "FIFA World Cup Album Confirmation",
                "hook": "My song made the official FIFA World Cup album!",
                "description": "The official announcement triggers a huge live reaction.",
                "tags": ["fifa", "worldcup", "music"],
                "engagement_question": "What achievement would make you react like this?",
            }
        ],
    }
    transcript = [TranscriptSegment(718, 749, "My song made the official FIFA album")]

    monkeypatch.setattr("clip_agent.pipeline.prepare_source", lambda *args, **kwargs: "video.mp4")
    monkeypatch.setattr("clip_agent.pipeline.probe_duration", lambda source: 6243.0)
    monkeypatch.setattr("clip_agent.pipeline.get_transcript", lambda *args, **kwargs: transcript)
    monkeypatch.setattr(
        "clip_agent.pipeline.load_cached_viral_analysis",
        lambda *args, **kwargs: cached_analysis,
    )
    monkeypatch.setattr(
        "clip_agent.pipeline.analyze_transcript_for_viral_moments",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("rendering should not re-analyze a matching cached source")
        ),
    )

    def fake_render(source, candidate, transcript_segments, output_dir, config, **kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        return RenderedClip(
            video_path=output_dir / "clip.mp4",
            srt_path=output_dir / "clip.srt",
            metadata_path=output_dir / "clip.json",
            candidate=candidate,
        )

    monkeypatch.setattr("clip_agent.pipeline.render_clip", fake_render)
    config = SimpleNamespace(openai_api_key="test", openai_analysis_model="gpt-5-mini")
    result = run_once(
        RunOptions(
            source=source_url,
            out_dir=tmp_path,
            max_clips=1,
            clip_length_seconds=45,
        ),
        config,
    )

    candidate = result.clips[0].candidate
    assert candidate.title == "FIFA World Cup Album Confirmation"
    assert candidate.hook == "My song made the official FIFA World Cup album!"
    assert candidate.tags == ("fifa", "worldcup", "music")
    assert candidate.engagement_question == "What achievement would make you react like this?"
