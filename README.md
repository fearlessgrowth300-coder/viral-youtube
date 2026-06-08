# AI Video Clipper Agent

This is a local starter agent for clipping videos you own or have permission to use. It can:

- accept a local video file, direct HLS/RTMP/HTTP media URL, or an explicitly allowed YouTube URL
- fetch and cache a YouTube transcript as soon as a link is pasted
- use OpenAI to rank transcript-grounded viral moments and prepare hooks, titles, descriptions, and tags
- transcribe with OpenAI when an API key is configured
- score funny, surprising, high-energy, or useful moments
- render vertical clips with bold burned-in captions, timed cutaway B-roll, and visual polish
- generate a 1280x720 viral thumbnail for one-minute and long videos using the selected payoff frame and OpenAI hook
- generate local movie-recap narration with Piper, with Windows and OpenAI voice fallbacks
- score titles, descriptions, tags, and pinned questions with a TubeBuddy/vidIQ-style SEO checklist
- write a posting review queue for YouTube, TikTok, and Instagram
- keep working by watching a folder of incoming recordings

It does not bypass platform rules. YouTube URL downloads are blocked unless `ALLOW_YOUTUBE_DOWNLOAD=true`, and publishing is blocked unless `PUBLISH_ENABLED=true` and the platform credentials are configured.

## Setup

```powershell
cd C:\Users\UPCOMING\ai-video-clipper-agent
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[ai,local-voice,publish,dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python -m piper.download_voices en_US-libritts-high --download-dir .tools\piper-voices
```

Edit `.env` with your keys. The packaged `imageio-ffmpeg` binary is used automatically if system `ffmpeg` is not installed.

## Process One Video

```powershell
.\.venv\Scripts\python -m clip_agent run `
  --source "C:\path\to\your-video.mp4" `
  --out runs\demo `
  --clips 3 `
  --platform youtube `
  --platform tiktok `
  --platform instagram
```

Outputs are written under `runs\demo`, including:

- rendered `.mp4` clips
- `.srt` caption files
- `manifest.json`
- `publish_queue.jsonl`

## Web App

```powershell
.\.venv\Scripts\python -m clip_agent web --host 127.0.0.1 --port 8010
```

Open:

```text
http://127.0.0.1:8010
```

The dashboard can start clip runs, show active jobs, preview rendered clips, and send reviewed queue entries to the configured publisher adapters. New runs default to bold captions, source-derived cutaway B-roll, polish filters, SEO scoring, and local Piper narration.

The SEO score is an in-app optimizer based on common title, keyword, description, tag, and engagement checks. It is not a direct TubeBuddy or vidIQ API integration.

For YouTube URLs, check `YouTube rights` in the form only for videos or live streams you own or have permission to clip.

The dashboard has two generation modes:

- `Viral Shorts` creates several ranked clips, starts close to the reaction payoff, shows a bold hook for the first five seconds, and adds an interaction question near the end.
- `Long video` creates one horizontal highlight at a requested duration from 1 to 120 minutes.

One-minute and long-video results include a custom YouTube thumbnail beside the rendered video. The Manual Upload dialog provides separate clip and thumbnail downloads. Direct YouTube publishing also attempts to apply the generated thumbnail after upload.

Use `Settings` to store TranscriptAPI, OpenAI, YouTube, TikTok, and Instagram credentials locally. Credentials are written under the ignored `.secrets` directory and are never returned to browser JavaScript.

When configured, TranscriptAPI is used as a YouTube transcript fallback before audio transcription. Exhausted credits, invalid keys, and rate limits are shown as clear job errors so the key can be replaced in Settings.

Pasting a YouTube link starts transcript analysis before rendering. The dashboard shows the top ranked timestamps, hooks, titles, descriptions, and tags. That transcript and OpenAI analysis are cached under the ignored `.cache` directory and reused when the clipping job starts.

## Production Website

The repository includes a production Docker image and `render.yaml` blueprint. The deployed service:

- uses the provider's permanent HTTPS URL
- stores runs, transcript caches, and saved settings on `/data`
- includes FFmpeg and the local Piper narration model
- exposes `/healthz` for hosting health checks
- protects the entire site with `APP_USERNAME` and `APP_PASSWORD`

Deploy on Render:

1. Push this repository to GitHub.
2. In Render, choose `New` then `Blueprint` and connect the repository.
3. Enter a strong `APP_PASSWORD`, plus `OPENAI_API_KEY` and `TRANSCRIPT_API_KEY`.
4. Keep the persistent disk mounted at `/data`.
5. After deployment, open the generated `https://...onrender.com` URL and sign in as `admin`.

This video workload requires a paid web service because persistent disks are unavailable on free web services. The included blueprint uses a Starter service and a 20 GB disk. Increase CPU, memory, or disk size for long or 4K jobs.

To use a domain you own, add it from the service's `Custom Domains` page, copy Render's DNS records to your domain provider, and verify the domain. Render then provisions HTTPS automatically.

Do not leave `APP_PASSWORD` empty on a public deployment. Keep API keys in Render environment variables or the protected Settings page, never in Git.

## Phone Access And Install

To use the app from a phone on the same Wi-Fi, run the server on all local network interfaces:

```powershell
.\.venv\Scripts\python -m clip_agent web --host 0.0.0.0 --port 8010
```

Find your PC's Wi-Fi IPv4 address:

```powershell
Get-NetIPAddress -AddressFamily IPv4
```

Then open this from your phone, replacing the IP:

```text
http://YOUR_PC_IP:8010
```

The app includes a web manifest, icon, and service worker for phone install support. Mobile browsers usually require HTTPS before showing a real install prompt, so use an HTTPS tunnel such as Cloudflare Tunnel when you want `Install app` or `Add to Home Screen` to work reliably:

```powershell
cloudflared tunnel --url http://127.0.0.1:8010
```

Open the generated `https://...trycloudflare.com` URL on your phone, then tap `Install app` or use the browser menu to add it to the home screen.

## Keep Working

Put new recordings into `incoming`, then run:

```powershell
.\.venv\Scripts\python -m clip_agent worker --watch-dir incoming --out runs --interval 60
```

For an owned live feed that FFmpeg can read, segment it into `incoming`:

```powershell
.\.venv\Scripts\python -m clip_agent record-live `
  --input "https://your-owned-stream.m3u8" `
  --out incoming `
  --segment-seconds 300
```

Run `record-live` and `worker` in separate terminals.

## Posting

The default is review-first. The agent writes `publish_queue.jsonl` entries with each rendered clip and suggested metadata.

To attempt posting:

```powershell
.\.venv\Scripts\python -m clip_agent publish --queue runs\demo\publish_queue.jsonl --approve
```

You still need official platform app setup:

- YouTube uses the Data API `videos.insert` upload endpoint and OAuth client secrets.
- TikTok uses the Content Posting API and requires `video.publish` approval for direct posting.
- Instagram uses the container flow for Reels. The video must be available at a public URL unless you extend the resumable upload flow.

## Notes

- Use `--transcript path\to\captions.srt` if you already have captions. This gives better moment selection and captions.
- Background audio should be music or sound you own or have licensed.
- Piper uses the GPL-3.0-or-later engine and the `en_US-libritts-high` LibriTTS voice data under CC BY 4.0. Attribution is written to clip metadata.
- If you enable generated voiceover, your post metadata includes a disclosure note.
