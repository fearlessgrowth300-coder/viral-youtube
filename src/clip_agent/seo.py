from __future__ import annotations

import re
from dataclasses import replace

from .models import ClipCandidate
from .scoring import is_generic_hook


POWER_WORDS = {
    "official",
    "reveals",
    "secret",
    "shocking",
    "wild",
    "crazy",
    "unexpected",
    "finally",
    "truth",
    "live",
    "instant",
    "challenge",
}
STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "from",
    "have",
    "into",
    "just",
    "that",
    "their",
    "there",
    "these",
    "this",
    "with",
    "your",
}


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def keyword_candidates(candidate: ClipCandidate) -> list[str]:
    values = [
        *candidate.tags,
        candidate.title,
        candidate.hook,
        candidate.description,
    ]
    keywords: list[str] = []
    seen: set[str] = set()
    for value in values:
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}", str(value or "")):
            normalized = word.lower().strip("-'")
            if (
                not normalized
                or normalized in STOP_WORDS
                or normalized in seen
                or normalized.isdigit()
            ):
                continue
            seen.add(normalized)
            keywords.append(normalized)
    return keywords[:12]


def normalize_tags(candidate: ClipCandidate, generation_mode: str) -> tuple[str, ...]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in [*candidate.tags, *keyword_candidates(candidate)]:
        tag = re.sub(r"[^a-zA-Z0-9]+", "", str(value or "")).lower()
        if len(tag) < 3 or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    if generation_mode == "short":
        required = [tag for tag in ("shorts", "viral") if tag not in seen]
        tags = tags[: max(0, 12 - len(required))]
        tags.extend(required)
    else:
        tags = [tag for tag in tags if tag != "shorts"]
        if "video" not in tags:
            tags.append("video")
    return tuple(tags[:12])


def title_variants(title: str, hook: str, keywords: tuple[str, ...]) -> tuple[str, ...]:
    base = clean_text(title).rstrip(".!?")
    hook_text = clean_text(hook).rstrip(".!?")
    primary_keyword = keywords[0].title() if keywords else ""
    variants = [
        base,
        hook_text,
        f"{base}: What Happened Next",
        f"{primary_keyword} Moment You Need to See" if primary_keyword else "",
    ]
    output: list[str] = []
    seen: set[str] = set()
    for value in variants:
        clean = clean_text(value)[:100]
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        output.append(clean)
    return tuple(output[:4])


def seo_score(
    title: str,
    description: str,
    tags: tuple[str, ...],
    engagement_question: str,
    keywords: tuple[str, ...],
) -> int:
    score = 25
    title_length = len(title)
    if 35 <= title_length <= 70:
        score += 20
    elif 25 <= title_length <= 85:
        score += 12
    lower_title = title.lower()
    if any(word in lower_title for word in POWER_WORDS):
        score += 12
    if keywords and keywords[0] in lower_title[:55]:
        score += 12
    if 100 <= len(description) <= 700:
        score += 12
    elif description:
        score += 6
    if 5 <= len(tags) <= 12:
        score += 12
    elif tags:
        score += 6
    if engagement_question.strip():
        score += 7
    return min(100, score)


def optimize_candidate(
    candidate: ClipCandidate,
    generation_mode: str = "short",
) -> ClipCandidate:
    title = clean_text(candidate.title)[:100] or "Must-See Video Highlight"
    hook = clean_text(candidate.hook or candidate.caption)
    if not hook or is_generic_hook(hook):
        hook = title
    tags = normalize_tags(candidate, generation_mode)
    keywords = tuple(keyword_candidates(replace(candidate, tags=tags))[:10])
    question = clean_text(candidate.engagement_question)
    description = clean_text(candidate.description or candidate.reason)
    if question and question.casefold() not in description.casefold():
        description = f"{description} {question}".strip()
    variants = title_variants(title, hook, keywords)
    return replace(
        candidate,
        title=title,
        hook=hook[:120],
        caption=clean_text(candidate.caption or hook)[:120],
        description=description[:1000],
        tags=tags,
        seo_score=seo_score(title, description, tags, question, keywords),
        seo_keywords=keywords,
        title_variants=variants,
    )
