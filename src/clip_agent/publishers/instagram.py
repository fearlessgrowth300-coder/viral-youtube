from __future__ import annotations

import time
from pathlib import Path

import requests

from .base import PublishMetadata, PublishResult, Publisher, require_publish_allowed
from ..config import AgentConfig


class InstagramPublisher(Publisher):
    platform = "instagram"

    def publish(
        self,
        video_path: Path,
        metadata: PublishMetadata,
        config: AgentConfig,
        approved: bool,
    ) -> PublishResult:
        require_publish_allowed(config, approved)
        if not config.instagram_access_token or not config.instagram_user_id:
            raise RuntimeError("INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID are required.")
        if not config.public_media_base_url:
            raise RuntimeError(
                "PUBLIC_MEDIA_BASE_URL is required. Instagram must fetch the rendered mp4 from a public URL."
            )

        base_url = f"https://graph.instagram.com/{config.instagram_api_version}"
        video_url = f"{config.public_media_base_url.rstrip('/')}/{video_path.name}"
        media = requests.post(
            f"{base_url}/{config.instagram_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": metadata.description[:2200],
                "access_token": config.instagram_access_token,
            },
            timeout=30,
        )
        media.raise_for_status()
        container_id = media.json().get("id")
        if not container_id:
            raise RuntimeError(f"Instagram did not return a container id: {media.text}")

        for _ in range(30):
            status = requests.get(
                f"{base_url}/{container_id}",
                params={
                    "fields": "status_code",
                    "access_token": config.instagram_access_token,
                },
                timeout=30,
            )
            status.raise_for_status()
            if status.json().get("status_code") == "FINISHED":
                break
            time.sleep(5)

        published = requests.post(
            f"{base_url}/{config.instagram_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": config.instagram_access_token},
            timeout=30,
        )
        published.raise_for_status()
        media_id = published.json().get("id")
        return PublishResult(
            platform=self.platform,
            status="published",
            external_id=media_id,
        )
