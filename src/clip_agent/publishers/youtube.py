from __future__ import annotations

from pathlib import Path

from .base import PublishMetadata, PublishResult, Publisher, require_publish_allowed
from ..config import AgentConfig


class YouTubePublisher(Publisher):
    platform = "youtube"

    def publish(
        self,
        video_path: Path,
        metadata: PublishMetadata,
        config: AgentConfig,
        approved: bool,
    ) -> PublishResult:
        require_publish_allowed(config, approved)
        if not config.youtube_client_secrets or not config.youtube_client_secrets.exists():
            raise RuntimeError("YOUTUBE_CLIENT_SECRETS must point to a Google OAuth client JSON file.")

        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise RuntimeError('Install the publish extra: python -m pip install -e ".[publish]"') from exc

        scopes = ["https://www.googleapis.com/auth/youtube.upload"]
        token_file = config.youtube_token_file
        token_file.parent.mkdir(parents=True, exist_ok=True)
        credentials = None
        if token_file.exists():
            credentials = Credentials.from_authorized_user_file(str(token_file), scopes)
        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(config.youtube_client_secrets), scopes)
            credentials = flow.run_local_server(port=0)
            token_file.write_text(credentials.to_json(), encoding="utf-8")

        service = build("youtube", "v3", credentials=credentials)
        body = {
            "snippet": {
                "title": metadata.title[:100],
                "description": metadata.description[:5000],
                "tags": metadata.tags[:30],
                "categoryId": config.youtube_category_id,
            },
            "status": {
                "privacyStatus": config.youtube_privacy_status,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": bool(metadata.has_ai_voiceover),
            },
        }
        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True),
        )
        response = request.execute()
        video_id = response.get("id")
        thumbnail_message = ""
        thumbnail_path = Path(metadata.thumbnail_path) if metadata.thumbnail_path else None
        if video_id and thumbnail_path and thumbnail_path.exists():
            try:
                service.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(
                        str(thumbnail_path),
                        mimetype="image/jpeg",
                        resumable=False,
                    ),
                ).execute()
                thumbnail_message = "Custom thumbnail uploaded."
            except Exception as exc:
                thumbnail_message = f"Video published, but YouTube rejected the custom thumbnail: {exc}"
        return PublishResult(
            platform=self.platform,
            status="published",
            url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
            external_id=video_id,
            message=thumbnail_message,
        )
