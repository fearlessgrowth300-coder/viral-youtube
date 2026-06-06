from __future__ import annotations

import hashlib
import html
import math
import re

from .models import ClipCandidate, TranscriptSegment


KEYWORD_WEIGHTS = {
    "funny": 5.0,
    "laugh": 5.5,
    "laughter": 5.5,
    "hilarious": 6.0,
    "joke": 3.0,
    "wow": 4.0,
    "crazy": 5.0,
    "insane": 5.0,
    "unbelievable": 5.0,
    "shocking": 5.0,
    "wild": 4.0,
    "best": 2.5,
    "secret": 4.0,
    "behind": 2.5,
    "revealed": 3.5,
    "mistake": 2.5,
    "fail": 3.5,
    "moment": 2.0,
    "wait": 3.0,
    "look": 2.0,
    "watch": 2.0,
    "never": 2.0,
    "first time": 3.0,
    "nobody": 2.5,
    "everyone": 2.0,
    "billion": 3.5,
    "massive": 2.0,
    "biggest": 3.0,
    "richest": 3.0,
    "problem": 2.0,
    "changed": 2.5,
    "important": 1.5,
}

GENRE_KEYWORDS = {
    "funny": ("funny", "laugh", "laughter", "hilarious", "joke", "can't", "what"),
    "crazy": ("crazy", "insane", "wild", "shocking", "unbelievable", "wow", "never"),
    "feel-good": ("feel", "proud", "happy", "finally", "good", "love", "win"),
    "business": ("billion", "money", "business", "industry", "project", "company", "energy", "scale"),
    "sports": ("scored", "goal", "save", "match", "game", "football", "win"),
}

GENERIC_HOOKS = {
    "THE HIDDEN PART",
    "WATCH THIS PART",
    "WATCH THIS",
    "BIG MOMENT",
    "WAIT FOR THIS",
    "GOOD MOMENT",
    "KEY REVEAL",
    "THIS IS BIG",
}

NOISE_WORDS = {
    "sun",
    "with",
    "yeah",
    "oh",
    "chat",
    "speed",
    "bro",
    "guys",
    "yo",
    "uh",
    "um",
}


def stable_jitter(text: str) -> float:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return int(digest[:4], 16) / 65535.0


def score_text(text: str, genre: str = "auto") -> float:
    text = normalize_text(text)
    lower = text.lower()
    score = 0.0
    for keyword, weight in KEYWORD_WEIGHTS.items():
        score += lower.count(keyword) * weight
    score += min(3.0, text.count("!") * 0.8)
    score += min(2.0, text.count("?") * 0.5)
    score += min(2.0, len(re.findall(r"\b[A-Z]{2,}\b", text)) * 0.3)
    if re.search(r"\b(i|you|we|they)\b.*\b(can't|cannot|won't|didn't|never|finally)\b", lower):
        score += 2.0
    if re.search(r"\b\d+(\.\d+)?\s*(million|billion|hours|years|people|percent|%)\b", lower):
        score += 2.5
    if re.search(r"\bbut\b|\bthen\b|\buntil\b|\bhowever\b", lower):
        score += 1.25
    for keyword in GENRE_KEYWORDS.get(genre, ()):
        if keyword in lower:
            score += 2.2
    score += min(1.5, math.log(max(1, len(text))) / 3.5)
    score += stable_jitter(text) * 0.2
    return score


def normalize_text(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&[a-zA-Z]+;", "", text)
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def collapse_repeated_words(text: str) -> str:
    words = normalize_text(text).split()
    collapsed: list[str] = []
    for word in words:
        clean = re.sub(r"[^a-z0-9]+", "", word.lower())
        previous = re.sub(r"[^a-z0-9]+", "", collapsed[-1].lower()) if collapsed else ""
        if clean and clean == previous:
            continue
        collapsed.append(word)
    return " ".join(collapsed)


def useful_text_score(text: str) -> float:
    words = [re.sub(r"[^a-z0-9]+", "", word.lower()) for word in normalize_text(text).split()]
    words = [word for word in words if word]
    if not words:
        return 0.0
    unique_ratio = len(set(words)) / len(words)
    noise_ratio = sum(1 for word in words if word in NOISE_WORDS) / len(words)
    return unique_ratio - noise_ratio


def clean_social_text(text: str) -> str:
    text = collapse_repeated_words(text)
    text = re.sub(r"\b([A-Za-z0-9]+)(?:\s+\1\b){1,}", r"\1", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" .,!?:;")


def title_case_phrase(text: str) -> str:
    small_words = {"a", "an", "and", "as", "at", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
    words = clean_social_text(text).split()
    title_words = []
    for index, word in enumerate(words):
        clean = word.strip(".,!?;:")
        if not clean:
            continue
        lower = clean.lower()
        if index > 0 and lower in small_words:
            title_words.append(lower)
        elif clean.isupper() and len(clean) <= 4:
            title_words.append(clean)
        else:
            title_words.append(lower.capitalize())
    return " ".join(title_words)


def is_generic_hook(text: str) -> bool:
    normalized = re.sub(r"[^A-Z0-9 ]+", "", normalize_text(text).upper()).strip()
    return normalized in GENERIC_HOOKS


def hook_phrase(text: str) -> str:
    clean = clean_social_text(text)
    chunks = [
        clean_social_text(chunk)
        for chunk in re.split(r"(?<=[.!?])\s+|[,;]\s+|\s+-\s+", clean)
        if len(clean_social_text(chunk).split()) >= 3
    ]
    if not chunks:
        chunks = [clean]
    chunks = [chunk for chunk in chunks if useful_text_score(chunk) >= 0.35] or chunks

    def chunk_score(chunk: str) -> float:
        lower = chunk.lower()
        score = score_text(chunk)
        if re.search(r"\b(because|but|until|why|how|can't|cannot|never|nobody|secret|behind)\b", lower):
            score += 3.0
        if re.search(r"\b\d+(\.\d+)?\s*(million|billion|hours|years|people|percent|%)\b", lower):
            score += 3.0
        if len(chunk.split()) > 14:
            score -= 1.0
        return score

    best = clean_social_text(max(chunks, key=chunk_score))
    best = re.sub(r"^(and|but|so|then|because|you know|what I mean)\s+", "", best, flags=re.I)
    words = best.split()
    if len(words) > 9:
        best = " ".join(words[:9])
    return best.strip(" .,!?:;")


def context_for_segment(
    segment: TranscriptSegment,
    segments: list[TranscriptSegment],
    before: float = 12.0,
    after: float = 18.0,
) -> str:
    start = max(0.0, segment.start - before)
    end = segment.end + after
    parts = [item.text for item in segments if item.end >= start and item.start <= end]
    return normalize_text(" ".join(parts))


def make_window(
    segment: TranscriptSegment,
    duration: float,
    clip_length: int,
    rank: int,
    score: float,
    context: str = "",
) -> ClipCandidate:
    if clip_length <= 180:
        lead_in = min(3.0, max(0.8, clip_length * 0.08))
    else:
        lead_in = min(30.0, max(8.0, clip_length * 0.08))
    start = max(0.0, segment.start - lead_in)
    if duration:
        start = min(start, max(0.0, duration - clip_length))
    end = min(duration or start + clip_length, start + clip_length)
    if end - start < 8 and duration:
        end = min(duration, start + 8)
    source_text = clean_social_text(context or segment.text)
    title = build_title(source_text, rank)
    hook = build_hook(source_text, rank)
    kind = classify_kind(source_text)
    engagement_question = build_engagement_question(source_text, kind)
    return ClipCandidate(
        start=round(start, 2),
        end=round(end, 2),
        title=title,
        reason=f"Viral score {score:.2f}: {source_text[:170]}",
        score=round(score, 3),
        kind=kind,
        caption=hook,
        voiceover=build_voiceover(source_text, hook),
        hook=hook,
        description=build_description(source_text, hook, engagement_question),
        tags=build_tags(source_text, kind),
        engagement_question=engagement_question,
    )


def build_title(text: str, rank: int) -> str:
    clean = clean_social_text(text)
    lower = clean.lower()
    phrase = hook_phrase(clean)
    words = (phrase or clean).split()
    phrase_title = title_case_phrase(" ".join(words[:8]))
    if "palestine" in lower:
        return "Streamer Reacts to Palestine Message"
    if "chat" in lower and ("laugh" in lower or "funny" in lower):
        return "Chat Could Not Stop Laughing"
    if "laugh" in lower or "funny" in lower:
        return phrase_title or "The Moment Everyone Started Laughing"
    if "crazy" in lower or "insane" in lower or "wild" in lower:
        return phrase_title or "The Moment That Changed Fast"
    if "billion" in lower or "massive" in lower or "biggest" in lower:
        return "The Scale Here Is Hard To Believe"
    if "behind" in lower or "secret" in lower or "revealed" in lower:
        return "What They Did Not Show You"
    if "can't" in lower or "cannot" in lower or "never" in lower:
        return "The Part Nobody Expected"
    if useful_text_score(phrase or clean) < 0.35:
        return f"Clip {rank} Highlight"
    title = title_case_phrase(" ".join(words[:8]))
    return title + ("..." if len(words) > 7 else "") if title else f"Clip {rank} Highlight"


def build_hook(text: str, rank: int) -> str:
    lower = text.lower()
    phrase = hook_phrase(text)
    phrase_upper = phrase.upper()
    if "laugh" in lower or "funny" in lower:
        return f"TRY NOT TO LAUGH: {phrase_upper}"[:86]
    if "crazy" in lower or "insane" in lower or "wild" in lower:
        return f"THIS REACTION CHANGES EVERYTHING: {phrase_upper}"[:86]
    if "billion" in lower or "massive" in lower or "biggest" in lower:
        return f"THIS IS BIGGER THAN YOU THINK: {phrase_upper}"[:86]
    if "behind" in lower or "secret" in lower or "revealed" in lower:
        return f"THEY DID NOT EXPECT THIS REVEAL: {phrase_upper}"[:86]
    if "can't" in lower or "cannot" in lower:
        return f"CAN YOU SPOT WHY HE CAN'T: {phrase_upper}"[:86]
    if "?" in text:
        return f"CAN YOU ANSWER THIS FIRST: {phrase_upper}"[:86]
    if phrase:
        return f"YOU WILL NOT EXPECT THIS: {phrase_upper}"[:86]
    return f"CAN YOU PREDICT THE TURN #{rank}"


def build_voiceover(text: str, hook: str) -> str:
    clean = clean_social_text(text)
    words = clean.split()
    summary = " ".join(words[:18])
    return f"{hook.title()}. {summary}".strip()


def build_engagement_question(text: str, kind: str) -> str:
    lower = clean_social_text(text).lower()
    if "speed" in lower:
        return "What's your favorite Speed moment?"
    if "palestine" in lower:
        return "What would you have said in this moment?"
    if kind == "funny":
        return "Did you laugh before the reaction ended?"
    if kind == "crazy":
        return "Did you expect the reaction to go this far?"
    if kind == "scale":
        return "Is this bigger than you expected?"
    if kind == "feel-good":
        return "What was your favorite part of this moment?"
    if re.search(r"football|goal|match|game|scored", lower):
        return "Was this the best moment of the game?"
    return "Would you have reacted the same way?"


def build_description(text: str, hook: str, engagement_question: str = "") -> str:
    clean = clean_social_text(text)
    hook = clean_social_text(hook)
    phrase = hook_phrase(clean)
    body = phrase or clean
    parts = [f"{hook}.", body[:220]]
    if engagement_question:
        parts.append(f"Question: {engagement_question}")
    return " ".join(part for part in parts if part).strip()


def classify_kind(text: str) -> str:
    lower = text.lower()
    if re.search(r"laugh|funny|joke|hilarious", lower):
        return "funny"
    if re.search(r"crazy|insane|wild|shocking|unbelievable|wow", lower):
        return "crazy"
    if re.search(r"billion|massive|biggest|richest|scale", lower):
        return "scale"
    if re.search(r"feel|proud|happy|finally|good|love", lower):
        return "feel-good"
    return "viral"


def build_tags(text: str, kind: str) -> tuple[str, ...]:
    tags = ["shorts", "viral", "bestmoments", kind]
    lower = text.lower()
    if "nigeria" in lower:
        tags.extend(["nigeria", "africa"])
    if "dangote" in lower:
        tags.extend(["dangote", "business"])
    if "football" in lower or "scored" in lower:
        tags.extend(["football", "sports"])
    if "laugh" in lower or "funny" in lower:
        tags.extend(["funny", "comedy"])
    if "palestine" in lower:
        tags.extend(["palestine", "livestream", "reaction"])
    if "chat" in lower or "stream" in lower or "speed" in lower:
        tags.extend(["livestream", "reaction"])
    return tuple(dict.fromkeys(tags))


def overlaps(candidate: ClipCandidate, selected: list[ClipCandidate]) -> bool:
    for existing in selected:
        overlap = max(0.0, min(candidate.end, existing.end) - max(candidate.start, existing.start))
        shortest = max(1.0, min(candidate.end - candidate.start, existing.end - existing.start))
        if overlap / shortest > 0.45:
            return True
    return False


def fallback_candidates(duration: float, max_clips: int, clip_length: int) -> list[ClipCandidate]:
    if duration <= 0:
        duration = max_clips * clip_length
    if duration <= clip_length:
        starts = [0.0]
    else:
        usable = max(0.0, duration - clip_length)
        if max_clips == 1:
            positions = [0.5]
        else:
            start_position = 0.16 if duration > 90 else 0.0
            end_position = 0.86
            step = (end_position - start_position) / max(1, max_clips - 1)
            positions = [start_position + step * index for index in range(max_clips)]
        starts = [min(usable, max(0.0, usable * position)) for position in positions]
    candidates: list[ClipCandidate] = []
    labels = ["Opening Highlight", "Early Highlight", "Middle Highlight", "Late Highlight", "Final Highlight"]
    hooks = ["START HERE", "KEY TURN", "WATCH THIS", "THE BIG PART", "FINAL MOMENT"]
    for index, start in enumerate(starts, start=1):
        label = labels[min(index - 1, len(labels) - 1)]
        if index > len(labels):
            label = f"Clip {index} Highlight"
        hook = hooks[min(index - 1, len(hooks) - 1)]
        if index > len(hooks):
            hook = f"CLIP {index}"
        candidates.append(
            ClipCandidate(
                start=round(start, 2),
                end=round(min(duration, start + clip_length), 2),
                title=label,
                reason="Timed fallback because no transcript moments were available.",
                score=0.1,
                caption=hook,
                voiceover=f"{label}.",
                hook=hook,
                description="Timed clip candidate. Add captions or connect transcription for smarter moment ranking.",
                tags=("shorts", "highlight"),
                engagement_question="Would you have reacted the same way?",
            )
        )
    return candidates


def select_candidates(
    segments: list[TranscriptSegment],
    duration: float,
    max_clips: int,
    clip_length: int,
    genre: str = "auto",
) -> list[ClipCandidate]:
    if not segments:
        return fallback_candidates(duration, max_clips, clip_length)

    scored = sorted(
        ((score_text(context_for_segment(segment, segments), genre=genre), segment) for segment in segments),
        key=lambda item: item[0],
        reverse=True,
    )
    selected: list[ClipCandidate] = []
    for rank, (score, segment) in enumerate(scored, start=1):
        candidate = make_window(
            segment,
            duration,
            clip_length,
            rank,
            score,
            context=context_for_segment(segment, segments),
        )
        if overlaps(candidate, selected):
            continue
        selected.append(candidate)
        if len(selected) >= max_clips:
            break

    if len(selected) < max_clips:
        for fallback in fallback_candidates(duration, max_clips - len(selected), clip_length):
            if not overlaps(fallback, selected):
                selected.append(fallback)

    return sorted(selected[:max_clips], key=lambda item: item.start)
