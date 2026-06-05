from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from .config import AgentConfig
from .ffmpeg import ffmpeg_bin
from .pipeline import RunOptions, run_once, watch_directory
from .publishers.dispatcher import publish_queue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clip_agent")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="Process one video source.")
    run.add_argument("--source", required=True)
    run.add_argument("--out", default="runs")
    run.add_argument("--transcript")
    run.add_argument("--clips", type=int, default=3)
    run.add_argument("--clip-length", type=int)
    run.add_argument("--horizontal", action="store_true")
    run.add_argument("--voiceover", action="store_true")
    run.add_argument("--render-quality", choices=["hd", "2k", "4k"], default="2k")
    run.add_argument("--max-transcribe-seconds", type=int)
    run.add_argument("--platform", action="append", default=[])

    worker = subcommands.add_parser("worker", help="Keep processing new videos in a folder.")
    worker.add_argument("--watch-dir", default="incoming")
    worker.add_argument("--out", default="runs")
    worker.add_argument("--interval", type=int, default=60)
    worker.add_argument("--clips", type=int, default=3)
    worker.add_argument("--clip-length", type=int)
    worker.add_argument("--platform", action="append", default=[])
    worker.add_argument("--once", action="store_true")

    record = subcommands.add_parser("record-live", help="Record an FFmpeg-readable live feed into segments.")
    record.add_argument("--input", required=True)
    record.add_argument("--out", default="incoming")
    record.add_argument("--segment-seconds", type=int, default=300)

    publish = subcommands.add_parser("publish", help="Publish reviewed queue entries.")
    publish.add_argument("--queue", required=True)
    publish.add_argument("--approve", action="store_true")

    web = subcommands.add_parser("web", help="Start the local web dashboard.")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8010)

    return parser


def command_run(args: argparse.Namespace, config: AgentConfig) -> None:
    clip_length = args.clip_length or config.default_clip_length_seconds
    options = RunOptions(
        source=args.source,
        out_dir=Path(args.out),
        transcript_path=Path(args.transcript) if args.transcript else None,
        max_clips=args.clips,
        clip_length_seconds=clip_length,
        vertical=not args.horizontal,
        enable_voiceover=args.voiceover,
        render_quality=args.render_quality,
        platforms=tuple(args.platform),
        max_transcribe_seconds=args.max_transcribe_seconds,
    )
    result = run_once(options, config)
    print(json.dumps(result.to_jsonable(), indent=2))


def command_worker(args: argparse.Namespace, config: AgentConfig) -> None:
    clip_length = args.clip_length or config.default_clip_length_seconds
    watch_directory(
        watch_dir=Path(args.watch_dir),
        out_dir=Path(args.out),
        config=config,
        interval=args.interval,
        max_clips=args.clips,
        clip_length_seconds=clip_length,
        platforms=tuple(args.platform),
        once=args.once,
    )


def command_record_live(args: argparse.Namespace) -> None:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "live_%Y%m%d_%H%M%S.mp4")
    command = [
        ffmpeg_bin(),
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        args.input,
        "-c",
        "copy",
        "-f",
        "segment",
        "-strftime",
        "1",
        "-segment_time",
        str(args.segment_seconds),
        "-reset_timestamps",
        "1",
        output_template,
    ]
    subprocess.run(command, check=True)


def command_publish(args: argparse.Namespace, config: AgentConfig) -> None:
    results = publish_queue(Path(args.queue), config, approved=args.approve)
    print(json.dumps([asdict(result) for result in results], indent=2))


def main() -> None:
    args = build_parser().parse_args()
    config = AgentConfig.from_env()
    if args.command == "run":
        command_run(args, config)
    elif args.command == "worker":
        command_worker(args, config)
    elif args.command == "record-live":
        command_record_live(args)
    elif args.command == "publish":
        command_publish(args, config)
    elif args.command == "web":
        from .web import run_web_server

        run_web_server(args)


if __name__ == "__main__":
    main()
