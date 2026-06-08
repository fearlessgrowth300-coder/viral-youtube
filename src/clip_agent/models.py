from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class ClipCandidate:
    start: float
    end: float
    title: str
    reason: str
    score: float
    kind: str = "highlight"
    caption: str = ""
    voiceover: str = ""
    hook: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    engagement_question: str = ""
    seo_score: int = 0
    seo_keywords: tuple[str, ...] = ()
    title_variants: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderedClip:
    video_path: Path
    srt_path: Path
    metadata_path: Path
    candidate: ClipCandidate
    thumbnail_path: Path | None = None
    thumbnail_text: str = ""
    has_ai_voiceover: bool = False
    voiceover_provider: str = ""
    broll_applied: bool = False
    broll_attribution: str = ""
    polished: bool = False


@dataclass
class PipelineResult:
    run_dir: Path
    source: str
    clips: list[RenderedClip] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "run_dir": str(self.run_dir),
            "source": self.source,
            "clips": [
                {
                    "video_path": str(clip.video_path),
                    "srt_path": str(clip.srt_path),
                    "metadata_path": str(clip.metadata_path),
                    "thumbnail_path": str(clip.thumbnail_path) if clip.thumbnail_path else "",
                    "thumbnail_text": clip.thumbnail_text,
                    "candidate": asdict(clip.candidate),
                    "has_ai_voiceover": clip.has_ai_voiceover,
                    "voiceover_provider": clip.voiceover_provider,
                    "broll_applied": clip.broll_applied,
                    "broll_attribution": clip.broll_attribution,
                    "polished": clip.polished,
                }
                for clip in self.clips
            ],
            "skipped": self.skipped,
        }
