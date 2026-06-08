from pathlib import Path

from PIL import Image

from clip_agent.models import ClipCandidate
from clip_agent.thumbnail import (
    THUMBNAIL_SIZE,
    render_thumbnail_from_frame,
    thumbnail_headline,
    thumbnail_timestamp,
)


def candidate() -> ClipCandidate:
    return ClipCandidate(
        start=60,
        end=120,
        title="Streamer discovers the impossible result",
        reason="The official announcement causes a huge reaction.",
        score=98,
        kind="shocking reaction",
        hook="My song made the official FIFA World Cup album!",
    )


def test_thumbnail_headline_uses_specific_hook() -> None:
    assert thumbnail_headline(candidate()) == "MY SONG MADE THE OFFICIAL FIFA WORLD CUP ALBUM!"


def test_thumbnail_timestamp_uses_frame_inside_selected_moment() -> None:
    assert thumbnail_timestamp(candidate()) == 64.5


def test_render_thumbnail_creates_youtube_size_jpeg(tmp_path: Path) -> None:
    frame = tmp_path / "frame.jpg"
    output = tmp_path / "thumbnail.jpg"
    Image.new("RGB", (720, 1280), (30, 100, 180)).save(frame)

    render_thumbnail_from_frame(frame, output, candidate())

    assert output.exists()
    with Image.open(output) as thumbnail:
        assert thumbnail.size == THUMBNAIL_SIZE
        assert thumbnail.format == "JPEG"
