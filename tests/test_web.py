import json
import base64
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

from clip_agent.config import AgentConfig
from clip_agent.models import TranscriptSegment
from clip_agent.web import (
    ASSETS_DIR,
    JobState,
    PROJECT_ROOT,
    WebState,
    analyze_source_content,
    basic_auth_matches,
    content_type_for_path,
    delete_clip,
    delete_run,
    media_url,
    publish_setup,
    resolve_project_path,
    sanitize_platforms,
    start_run_job,
)


def test_resolve_project_path_allows_project_files() -> None:
    resolved = resolve_project_path("samples/sample.srt")
    assert resolved == (PROJECT_ROOT / "samples" / "sample.srt").resolve()


def test_resolve_project_path_blocks_outside_files() -> None:
    try:
        resolve_project_path(str(Path("C:/Windows/System32/drivers/etc/hosts")))
    except PermissionError:
        return
    raise AssertionError("expected PermissionError")


def test_sanitize_platforms() -> None:
    assert sanitize_platforms(["youtube", "bad", "tiktok"]) == ("youtube", "tiktok")


def test_media_url_uses_media_route() -> None:
    assert media_url(PROJECT_ROOT / "samples" / "sample.mp4").startswith("/media?path=")


def test_basic_auth_is_optional_without_password() -> None:
    assert basic_auth_matches("", "admin", "")


def test_basic_auth_requires_exact_credentials() -> None:
    encoded = base64.b64encode(b"admin:strong-password").decode("ascii")
    assert basic_auth_matches(f"Basic {encoded}", "admin", "strong-password")
    assert not basic_auth_matches(f"Basic {encoded}", "admin", "different")
    assert not basic_auth_matches("Bearer token", "admin", "strong-password")


def test_pwa_assets_exist_with_manifest_content_type() -> None:
    assert (ASSETS_DIR / "manifest.webmanifest").exists()
    assert (ASSETS_DIR / "service-worker.js").exists()
    assert (ASSETS_DIR / "icon.svg").exists()
    assert content_type_for_path(ASSETS_DIR / "manifest.webmanifest") == "application/manifest+json"


def minimal_config() -> AgentConfig:
    return AgentConfig(
        openai_api_key=None,
        openai_analysis_model="gpt-5-mini",
        openai_transcribe_model="gpt-4o-mini-transcribe",
        openai_tts_model="gpt-4o-mini-tts",
        openai_tts_voice="cedar",
        allow_youtube_download=False,
        publish_enabled=False,
        require_manual_review=True,
        background_audio_path=None,
        background_volume=0.12,
        default_clip_length_seconds=45,
        youtube_client_secrets=None,
        youtube_token_file=PROJECT_ROOT / ".secrets" / "youtube.json",
        youtube_privacy_status="private",
        youtube_category_id="24",
        tiktok_access_token=None,
        tiktok_privacy_level="SELF_ONLY",
        instagram_access_token=None,
        instagram_user_id=None,
        instagram_api_version="v23.0",
        public_media_base_url=None,
    )


def test_start_run_rejects_youtube_without_permission() -> None:
    try:
        start_run_job(
            {
                "source": "https://youtu.be/kApsw7gsALs?si=M6TAGwcY6T5-RyHm",
                "clips": 1,
                "clip_length": 10,
            },
            WebState(minimal_config()),
        )
    except ValueError as exc:
        assert "I own/have permission" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_analyze_source_content_fetches_transcript_and_ranks_moments(monkeypatch) -> None:
    monkeypatch.setattr(
        "clip_agent.web.analyze_source",
        lambda source: {
            "source": source,
            "title": "Test video",
            "is_youtube": True,
            "duration": 120,
            "duration_string": "2:00",
            "analysis_error": "",
        },
    )
    monkeypatch.setattr(
        "clip_agent.web.fetch_transcript_api",
        lambda source, run_dir, config: [
            TranscriptSegment(40, 45, "This reaction got completely crazy"),
        ],
    )
    monkeypatch.setattr(
        "clip_agent.web.analyze_transcript_for_viral_moments",
        lambda source, transcript, duration, config: {
            "provider": "openai",
            "summary": "Strongest moment",
            "moments": [{"start": 40, "end": 45, "title": "Crazy reaction"}],
        },
    )
    config = replace(minimal_config(), transcript_api_key="transcript-key")

    analysis = analyze_source_content("https://youtu.be/analysis001", config)

    assert analysis["transcript_status"] == "ready"
    assert analysis["transcript_segments"] == 1
    assert analysis["viral_analysis"]["provider"] == "openai"


def test_analyze_source_content_reports_missing_transcript_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "clip_agent.web.analyze_source",
        lambda source: {"source": source, "title": "Test video", "is_youtube": True},
    )
    analysis = analyze_source_content(
        "https://youtu.be/analysis002",
        minimal_config(),
    )
    assert analysis["transcript_status"] == "not_configured"


def test_start_run_rejects_unavailable_youtube_before_creating_job(monkeypatch) -> None:
    def fake_analyze_source(source: str) -> dict:
        return {"analysis_error": "ERROR: [youtube] abc: This video is not available"}

    monkeypatch.setattr("clip_agent.web.analyze_source", fake_analyze_source)
    state = WebState(minimal_config())
    try:
        start_run_job(
            {
                "source": "https://www.youtube.com/live/18leU88yGEU?si=x",
                "allow_youtube_download": True,
                "clips": 1,
                "clip_length": 10,
            },
            state,
        )
    except ValueError as exc:
        assert "unavailable" in str(exc)
        assert state.list_jobs() == []
    else:
        raise AssertionError("expected ValueError")


def test_start_run_long_mode_forces_one_horizontal_video(monkeypatch) -> None:
    captured = {}

    class FakeThread:
        def __init__(self, target, args, daemon):
            captured["options"] = args[1]

        def start(self):
            return None

    monkeypatch.setattr("clip_agent.web.threading.Thread", FakeThread)
    start_run_job(
        {
            "use_sample": True,
            "generation_mode": "long",
            "long_duration_seconds": 600,
            "horizontal": False,
            "clips": 8,
        },
        WebState(minimal_config()),
    )
    options = captured["options"]
    assert options.generation_mode == "long"
    assert options.max_clips == 1
    assert options.clip_length_seconds == 600
    assert options.vertical is False
    assert options.live_capture_seconds >= 600


def test_cancel_job_marks_running_job_as_cancelling() -> None:
    state = WebState(minimal_config())
    state.put_job(JobState(id="job-1", source="sample.mp4", status="running", message="Rendering"))
    job = state.request_cancel("job-1")
    assert job is not None
    assert job["status"] == "cancelling"
    assert job["cancel_requested"] is True
    assert state.should_cancel("job-1") is True


def test_delete_job_hides_job_and_requests_cancel() -> None:
    state = WebState(minimal_config())
    state.put_job(JobState(id="job-2", source="sample.mp4", status="running", message="Rendering"))
    result = state.delete_job("job-2")
    assert result == {"deleted": True, "job_id": "job-2"}
    assert state.list_jobs() == []
    assert state.should_cancel("job-2") is True
    assert state.update_job("job-2", status="failed") is False


def test_delete_clip_updates_manifest_and_queue() -> None:
    run_dir = PROJECT_ROOT / "runs" / f"pytest-delete-{uuid.uuid4().hex}"
    clips_dir = run_dir / "clips"
    clips_dir.mkdir(parents=True)
    video = clips_dir / "000001-test.mp4"
    srt = clips_dir / "000001-test.srt"
    metadata = clips_dir / "000001-test.json"
    thumbnail = clips_dir / "000001-test-thumbnail.jpg"
    voiceover = clips_dir / "000001-test-voiceover.wav"
    for path in (video, srt, metadata, thumbnail, voiceover):
        path.write_text("x", encoding="utf-8")
    manifest = {
        "run_dir": str(run_dir),
        "source": "sample",
        "clips": [
            {
                "video_path": str(video),
                "srt_path": str(srt),
                "metadata_path": str(metadata),
                "thumbnail_path": str(thumbnail),
                "candidate": {"title": "Test"},
            }
        ],
        "skipped": [],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "publish_queue.jsonl").write_text(
        json.dumps({"platform": "youtube", "video_path": str(video), "status": "ready_for_review"}) + "\n",
        encoding="utf-8",
    )
    try:
        result = delete_clip(str(video), str(run_dir))
        assert result["deleted"] == "clip"
        assert not video.exists()
        assert not srt.exists()
        assert not metadata.exists()
        assert not thumbnail.exists()
        assert not voiceover.exists()
        updated = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert updated["clips"] == []
        assert not (run_dir / "publish_queue.jsonl").exists()
    finally:
        if run_dir.exists():
            shutil.rmtree(run_dir)


def test_delete_run_removes_run_directory() -> None:
    run_dir = PROJECT_ROOT / "runs" / f"pytest-delete-{uuid.uuid4().hex}"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text('{"clips": []}', encoding="utf-8")
    delete_run(str(run_dir))
    assert not run_dir.exists()


def test_publish_setup_reports_disabled_publish() -> None:
    setup = publish_setup(minimal_config())
    assert setup["enabled"] is False
    assert setup["youtube"]["ready"] is False
    assert "PUBLISH_ENABLED=true" in setup["youtube"]["message"]
    assert setup["tiktok"]["ready"] is False
    assert "PUBLISH_ENABLED=true" in setup["tiktok"]["message"]
