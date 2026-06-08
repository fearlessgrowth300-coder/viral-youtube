from pathlib import Path

from clip_agent.public_movies import (
    choose_movie_file,
    clean_catalog_text,
    resolve_public_domain_movie,
    search_public_domain_movies,
)


class FakeResponse:
    def __init__(self, payload=None, content=b"", status_code=200):
        self.payload = payload or {}
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def test_clean_catalog_text_removes_html() -> None:
    assert clean_catalog_text("<p>Classic &amp; funny</p>") == "Classic & funny"


def test_search_public_domain_movies_filters_license(monkeypatch) -> None:
    payload = {
        "response": {
            "numFound": 2,
            "docs": [
                {
                    "identifier": "his_girl_friday",
                    "title": "His Girl Friday",
                    "year": 1940,
                    "downloads": 100,
                    "licenseurl": "http://creativecommons.org/licenses/publicdomain/",
                },
                {
                    "identifier": "not_public",
                    "title": "Not public",
                    "licenseurl": "https://creativecommons.org/licenses/by-nc/4.0/",
                },
            ],
        }
    }
    monkeypatch.setattr(
        "clip_agent.public_movies.requests.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    result = search_public_domain_movies("comedy")
    assert [movie["identifier"] for movie in result["movies"]] == ["his_girl_friday"]


def test_empty_movie_search_returns_modern_featured_films() -> None:
    result = search_public_domain_movies("")
    assert result["featured"] is True
    assert result["movies"][0]["year"] == "2022"
    assert result["movies"][0]["identifier"] == "charge-blender-open-movie-1608p"


def test_choose_movie_file_prefers_higher_quality() -> None:
    selected = choose_movie_file(
        [
            {"name": "movie_512kb.mp4", "size": "300000000", "format": "512Kb MPEG4", "height": "360"},
            {"name": "movie.mp4", "size": "600000000", "format": "h.264", "height": "720"},
        ]
    )
    assert selected is not None
    assert selected["name"] == "movie.mp4"


def test_resolve_public_movie_returns_mp4_and_downloads_subtitle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    metadata = {
        "metadata": {
            "identifier": "public_movie",
            "title": "Public Movie",
            "mediatype": "movies",
            "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
        },
        "files": [
            {"name": "movie.mp4", "size": "600000000", "format": "h.264", "height": "720"},
            {"name": "movie.en.srt", "size": "50", "format": "SubRip"},
        ],
    }

    def fake_get(url, **kwargs):
        if "/metadata/" in url:
            return FakeResponse(metadata)
        return FakeResponse(content=b"1\n00:00:00,000 --> 00:00:02,000\nHello\n")

    monkeypatch.setattr("clip_agent.public_movies.requests.get", fake_get)
    monkeypatch.setattr(
        "clip_agent.public_movies.download_subtitle",
        lambda identifier, subtitle_file: tmp_path / "captions.srt",
    )
    result = resolve_public_domain_movie("public_movie")
    assert result["source_url"].endswith("/public_movie/movie.mp4")
    assert result["transcript_path"].endswith("captions.srt")
