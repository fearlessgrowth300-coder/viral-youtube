from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import AgentConfig


class PublishBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishMetadata:
    title: str
    description: str
    tags: list[str]
    has_ai_voiceover: bool = False


@dataclass(frozen=True)
class PublishResult:
    platform: str
    status: str
    url: str | None = None
    external_id: str | None = None
    message: str = ""


def require_publish_allowed(config: AgentConfig, approved: bool) -> None:
    if not config.publish_enabled:
        raise PublishBlocked("PUBLISH_ENABLED is false.")
    if config.require_manual_review and not approved:
        raise PublishBlocked("Manual review is required. Pass --approve after reviewing clips.")


class Publisher:
    platform = "base"

    def publish(
        self,
        video_path: Path,
        metadata: PublishMetadata,
        config: AgentConfig,
        approved: bool,
    ) -> PublishResult:
        raise NotImplementedError
