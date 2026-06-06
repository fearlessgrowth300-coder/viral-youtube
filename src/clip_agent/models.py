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


@dataclass(frozen=True)
class RenderedClip:
    video_path: Path
    srt_path: Path
    metadata_path: Path
    candidate: ClipCandidate
    has_ai_voiceover: bool = False


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
                    "candidate": asdict(clip.candidate),
                    "has_ai_voiceover": clip.has_ai_voiceover,
                }
                for clip in self.clips
            ],
            "skipped": self.skipped,
        }
