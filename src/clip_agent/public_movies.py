from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .paths import CACHE_ROOT


ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"
ARCHIVE_METADATA_URL = "https://archive.org/metadata"
ARCHIVE_DOWNLOAD_URL = "https://archive.org/download"
PUBLIC_DOMAIN_QUERY = (
    'mediatype:movies AND collection:feature_films AND licenseurl:*publicdomain* '
    'AND (format:"h.264" OR format:"512Kb MPEG4")'
)
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


class PublicMovieError(RuntimeError):
    pass


def clean_catalog_text(value: Any, limit: int = 360) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def safe_search_terms(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9' -]+", " ", value)
    return re.sub(r"\s+", " ", clean).strip()[:80]


def public_domain_license(value: Any) -> str:
    values = value if isinstance(value, list) else [value]
    for item in values:
        license_url = str(item or "")
        if "publicdomain" in license_url.lower():
            return license_url
    return ""


def archive_thumbnail_url(identifier: str) -> str:
    return f"https://archive.org/services/img/{quote(identifier, safe='')}"


def archive_details_url(identifier: str) -> str:
    return f"https://archive.org/details/{quote(identifier, safe='')}"


def archive_download_url(identifier: str, filename: str) -> str:
    return (
        f"{ARCHIVE_DOWNLOAD_URL}/{quote(identifier, safe='')}/"
        f"{quote(filename, safe='')}"
    )


def search_public_domain_movies(
    query: str = "",
    page: int = 1,
    rows: int = 12,
) -> dict[str, Any]:
    terms = safe_search_terms(query) or "charlie chaplin"
    search_query = f"{PUBLIC_DOMAIN_QUERY} AND (title:({terms}) OR description:({terms}))"
    response = requests.get(
        ARCHIVE_SEARCH_URL,
        params={
            "q": search_query,
            "fl[]": [
                "identifier",
                "title",
                "description",
                "year",
                "downloads",
                "licenseurl",
            ],
            "sort[]": "downloads desc",
            "rows": max(1, min(24, int(rows))),
            "page": max(1, int(page)),
            "output": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json().get("response") or {}
    movies: list[dict[str, Any]] = []
    for document in payload.get("docs") or []:
        identifier = str(document.get("identifier") or "")
        license_url = public_domain_license(document.get("licenseurl"))
        if not IDENTIFIER_RE.fullmatch(identifier) or not license_url:
            continue
        movies.append(
            {
                "identifier": identifier,
                "title": clean_catalog_text(document.get("title") or identifier, 120),
                "description": clean_catalog_text(document.get("description")),
                "year": clean_catalog_text(document.get("year"), 12),
                "downloads": int(document.get("downloads") or 0),
                "license_url": license_url,
                "thumbnail_url": archive_thumbnail_url(identifier),
                "details_url": archive_details_url(identifier),
                "provider": "Internet Archive",
            }
        )
    return {
        "query": terms,
        "page": max(1, int(page)),
        "total": int(payload.get("numFound") or len(movies)),
        "movies": movies,
    }


def file_size(item: dict[str, Any]) -> int:
    try:
        return int(item.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def video_file_score(item: dict[str, Any]) -> tuple[int, int, int]:
    name = str(item.get("name") or "").lower()
    file_format = str(item.get("format") or "").lower()
    try:
        height = int(item.get("height") or 0)
    except (TypeError, ValueError):
        height = 0
    preferred_format = 2 if "h.264" in file_format else 1 if "mpeg4" in file_format else 0
    low_quality_penalty = -1 if "512kb" in name or "512kb" in file_format else 0
    return (preferred_format + low_quality_penalty, height, file_size(item))


def choose_movie_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        item
        for item in files
        if str(item.get("name") or "").lower().endswith((".mp4", ".m4v"))
        and file_size(item) > 1_000_000
        and file_size(item) < 8_000_000_000
        and not str(item.get("name") or "").lower().endswith(("_thumb.mp4", "_sample.mp4"))
    ]
    return max(candidates, key=video_file_score) if candidates else None


def choose_subtitle_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        item
        for item in files
        if str(item.get("name") or "").lower().endswith((".srt", ".vtt"))
        and 0 < file_size(item) < 8_000_000
    ]
    if not candidates:
        return None

    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        name = str(item.get("name") or "").lower()
        english = 2 if re.search(r"(^|[._-])en(g|glish)?([._-]|$)", name) else 0
        srt = 1 if name.endswith(".srt") else 0
        return (english, srt, file_size(item))

    return max(candidates, key=score)


def download_subtitle(
    identifier: str,
    subtitle_file: dict[str, Any],
    cache_root: Path = CACHE_ROOT / "public-movies",
) -> Path:
    filename = str(subtitle_file.get("name") or "")
    suffix = Path(filename).suffix.lower()
    destination = cache_root / identifier / f"captions{suffix}"
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    response = requests.get(
        archive_download_url(identifier, filename),
        timeout=45,
    )
    response.raise_for_status()
    if len(response.content) > 8_000_000:
        raise PublicMovieError("Subtitle file is too large.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)
    return destination


def resolve_public_domain_movie(identifier: str) -> dict[str, Any]:
    if not IDENTIFIER_RE.fullmatch(identifier):
        raise PublicMovieError("Invalid public movie identifier.")
    response = requests.get(f"{ARCHIVE_METADATA_URL}/{identifier}", timeout=30)
    response.raise_for_status()
    payload = response.json()
    metadata = payload.get("metadata") or {}
    license_url = public_domain_license(metadata.get("licenseurl"))
    if metadata.get("mediatype") != "movies" or not license_url:
        raise PublicMovieError(
            "This item is not marked as a public-domain movie by Internet Archive."
        )

    movie_file = choose_movie_file(payload.get("files") or [])
    if not movie_file:
        raise PublicMovieError("No compatible MP4 file is available for this movie.")
    subtitle_file = choose_subtitle_file(payload.get("files") or [])
    transcript_path = ""
    transcript_error = ""
    if subtitle_file:
        try:
            transcript_path = str(download_subtitle(identifier, subtitle_file))
        except Exception as exc:
            transcript_error = str(exc)

    filename = str(movie_file.get("name") or "")
    return {
        "identifier": identifier,
        "title": clean_catalog_text(metadata.get("title") or identifier, 120),
        "description": clean_catalog_text(metadata.get("description")),
        "year": clean_catalog_text(metadata.get("year") or metadata.get("date"), 12),
        "license_url": license_url,
        "rights": clean_catalog_text(metadata.get("rights"), 240),
        "thumbnail_url": archive_thumbnail_url(identifier),
        "details_url": archive_details_url(identifier),
        "source_url": archive_download_url(identifier, filename),
        "filename": filename,
        "file_size": file_size(movie_file),
        "transcript_path": transcript_path,
        "transcript_error": transcript_error,
        "provider": "Internet Archive",
    }
