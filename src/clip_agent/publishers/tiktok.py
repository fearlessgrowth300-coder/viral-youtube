from __future__ import annotations

from pathlib import Path

import requests

from .base import PublishMetadata, PublishResult, Publisher, require_publish_allowed
from ..config import AgentConfig


class TikTokPublisher(Publisher):
    platform = "tiktok"

    def publish(
        self,
        video_path: Path,
        metadata: PublishMetadata,
        config: AgentConfig,
        approved: bool,
    ) -> PublishResult:
        require_publish_allowed(config, approved)
        if not config.tiktok_access_token:
            raise RuntimeError("TIKTOK_ACCESS_TOKEN is required.")

        headers = {
            "Authorization": f"Bearer {config.tiktok_access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        creator = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
            headers=headers,
            timeout=30,
        )
        creator.raise_for_status()
        creator_data = creator.json().get("data", {})
        privacy_options = creator_data.get("privacy_level_options") or ["SELF_ONLY"]
        privacy_level = config.tiktok_privacy_level
        if privacy_level not in privacy_options:
            privacy_level = privacy_options[0]

        size = video_path.stat().st_size
        payload = {
            "post_info": {
                "title": metadata.title[:150],
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            },
        }
        init = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers=headers,
            json=payload,
            timeout=30,
        )
        init.raise_for_status()
        init_data = init.json().get("data", {})
        upload_url = init_data.get("upload_url")
        publish_id = init_data.get("publish_id")
        if not upload_url:
            raise RuntimeError(f"TikTok did not return upload_url: {init.text}")

        with video_path.open("rb") as video:
            upload = requests.put(
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(size),
                    "Content-Range": f"bytes 0-{size - 1}/{size}",
                },
                data=video,
                timeout=120,
            )
        upload.raise_for_status()
        return PublishResult(
            platform=self.platform,
            status="uploaded",
            external_id=publish_id,
            message="TikTok accepted the upload. Poll publish status in the TikTok developer flow if needed.",
        )
