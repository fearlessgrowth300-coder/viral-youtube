from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .base import PublishBlocked, PublishMetadata, PublishResult
from .instagram import InstagramPublisher
from .tiktok import TikTokPublisher
from .youtube import YouTubePublisher
from ..config import AgentConfig
from ..models import RenderedClip


PUBLISHERS = {
    "youtube": YouTubePublisher(),
    "tiktok": TikTokPublisher(),
    "instagram": InstagramPublisher(),
}


def metadata_for_clip(clip: RenderedClip) -> PublishMetadata:
    tags = list(clip.candidate.tags or ("shorts", "viral", clip.candidate.kind))
    tags = list(dict.fromkeys([tag.strip("#").replace(" ", "") for tag in tags if tag]))
    disclosure = "\n\nDisclosure: contains AI-generated voiceover." if clip.has_ai_voiceover else ""
    hashtags = " ".join(f"#{tag}" for tag in tags[:8])
    description = clip.candidate.description or clip.candidate.reason
    return PublishMetadata(
        title=clip.candidate.title[:100],
        description=f"{clip.candidate.hook or clip.candidate.caption}\n\n{description}\n\n{hashtags}{disclosure}",
        tags=tags,
        has_ai_voiceover=clip.has_ai_voiceover,
    )


def append_publish_queue(
    queue_path: Path,
    clips: list[RenderedClip],
    platforms: list[str],
) -> None:
    if not platforms or not clips:
        return
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with queue_path.open("a", encoding="utf-8") as queue:
        for clip in clips:
            metadata = metadata_for_clip(clip)
            for platform in platforms:
                entry = {
                    "status": "ready_for_review",
                    "platform": platform,
                    "video_path": str(clip.video_path),
                    "metadata": asdict(metadata),
                }
                queue.write(json.dumps(entry, ensure_ascii=True) + "\n")


def publish_queue(queue_path: Path, config: AgentConfig, approved: bool = False) -> list[PublishResult]:
    results: list[PublishResult] = []
    if not queue_path.exists():
        raise FileNotFoundError(queue_path)
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("status") not in {"ready_for_review", "failed"}:
            continue
        platform = entry["platform"]
        publisher = PUBLISHERS.get(platform)
        if not publisher:
            results.append(PublishResult(platform=platform, status="skipped", message="Unknown platform"))
            continue
        metadata = PublishMetadata(**entry["metadata"])
        try:
            results.append(
                publisher.publish(Path(entry["video_path"]), metadata, config, approved=approved)
            )
        except PublishBlocked as exc:
            results.append(PublishResult(platform=platform, status="blocked", message=str(exc)))
        except Exception as exc:
            results.append(PublishResult(platform=platform, status="failed", message=str(exc)))
    return results
