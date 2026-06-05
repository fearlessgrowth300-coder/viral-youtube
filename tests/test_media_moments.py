from clip_agent.media_moments import media_candidates_from_features, parse_audio_levels, parse_scene_times


def test_parse_audio_levels_from_ffmpeg_metadata() -> None:
    output = """
[Parsed_ametadata_2] frame:1    pts:44100   pts_time:1
[Parsed_ametadata_2] lavfi.astats.Overall.RMS_level=-21.080724
[Parsed_ametadata_2] frame:2    pts:88200   pts_time:2
[Parsed_ametadata_2] lavfi.astats.Overall.RMS_level=-40.5
"""
    assert parse_audio_levels(output) == [(1.0, -21.080724), (2.0, -40.5)]


def test_parse_scene_times_from_showinfo() -> None:
    output = "[Parsed_showinfo_1] n:  12 pts: 184320 pts_time:12 pos:123 fmt:yuv420p"
    assert parse_scene_times(output) == [12.0]


def test_media_candidates_pick_audio_peak_instead_of_intro() -> None:
    audio = [(float(second), -48.0) for second in range(0, 180, 5)]
    audio.extend([(92.0, -16.0), (97.0, -18.0)])
    clips = media_candidates_from_features(
        audio,
        scene_times=[],
        duration=180,
        max_clips=1,
        clip_length=30,
    )
    assert len(clips) == 1
    assert clips[0].start > 60
    assert "Media score" in clips[0].reason
