from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .ffmpeg import ffmpeg_bin
from .models import ClipCandidate
from .scoring import build_hook, is_generic_hook


THUMBNAIL_SIZE = (1280, 720)
FONT_CANDIDATES = tuple(
    Path(value)
    for value in (
        os.getenv("THUMBNAIL_FONT_PATH", ""),
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )
    if value
)


def thumbnail_headline(candidate: ClipCandidate, max_words: int = 10) -> str:
    text = candidate.hook or candidate.caption or candidate.title
    if not text or is_generic_hook(text):
        text = build_hook(candidate.reason or candidate.title, 1)
    clean = re.sub(r"\s+", " ", str(text)).strip(" .,:;-")
    words = clean.split()
    if len(words) > max_words:
        clean = " ".join(words[:max_words])
    return clean.upper()[:72]


def thumbnail_timestamp(candidate: ClipCandidate) -> float:
    duration = max(1.0, candidate.end - candidate.start)
    offset = min(max(1.5, duration * 0.08), 4.5, max(1.0, duration - 1.0))
    return max(0.0, candidate.start + offset)


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def wrap_thumbnail_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    max_lines: int = 3,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        width = draw.textbbox((0, 0), candidate, font=font, stroke_width=4)[2]
        if current and width > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines[:max_lines]


def prepare_thumbnail_background(frame: Image.Image) -> Image.Image:
    frame = ImageEnhance.Contrast(frame.convert("RGB")).enhance(1.08)
    frame = ImageEnhance.Color(frame).enhance(1.12)
    width, height = frame.size
    if width / max(1, height) >= 1.35:
        return ImageOps.fit(frame, THUMBNAIL_SIZE, method=Image.Resampling.LANCZOS)

    background = ImageOps.fit(frame, THUMBNAIL_SIZE, method=Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(radius=28))
    foreground = ImageOps.contain(frame, THUMBNAIL_SIZE, method=Image.Resampling.LANCZOS)
    x = (THUMBNAIL_SIZE[0] - foreground.width) // 2
    y = (THUMBNAIL_SIZE[1] - foreground.height) // 2
    background.paste(foreground, (x, y))
    return background


def add_readability_gradient(image: Image.Image) -> None:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pixels = overlay.load()
    width, height = image.size
    for y in range(height):
        bottom_ratio = max(0.0, (y - height * 0.42) / (height * 0.58))
        top_ratio = max(0.0, (height * 0.15 - y) / (height * 0.15))
        alpha = int(min(210, bottom_ratio * 210 + top_ratio * 90))
        for x in range(width):
            pixels[x, y] = (0, 0, 0, alpha)
    image.alpha_composite(overlay)


def render_thumbnail_from_frame(
    frame_path: Path,
    destination: Path,
    candidate: ClipCandidate,
) -> Path:
    with Image.open(frame_path) as source:
        canvas = prepare_thumbnail_background(source).convert("RGBA")

    add_readability_gradient(canvas)
    draw = ImageDraw.Draw(canvas)
    badge_font = load_font(30)
    badge_text = re.sub(r"[^A-Z0-9 ]+", "", (candidate.kind or "HIGHLIGHT").upper())[:22]
    badge_width = draw.textbbox((0, 0), badge_text, font=badge_font)[2] + 38
    draw.rounded_rectangle((50, 42, 50 + badge_width, 94), radius=8, fill=(250, 204, 21, 255))
    draw.text((69, 52), badge_text, font=badge_font, fill=(10, 14, 12, 255))

    headline = thumbnail_headline(candidate)
    font_size = 86
    title_font = load_font(font_size)
    lines = wrap_thumbnail_text(draw, headline, title_font, 1120)
    while font_size > 56 and len(lines) >= 3:
        line_width = max(
            draw.textbbox((0, 0), line, font=title_font, stroke_width=5)[2]
            for line in lines
        )
        if line_width <= 1120:
            break
        font_size -= 6
        title_font = load_font(font_size)
        lines = wrap_thumbnail_text(draw, headline, title_font, 1120)

    line_height = int(font_size * 1.08)
    block_height = line_height * len(lines)
    y = max(330, 670 - block_height)
    draw.rectangle((50, y - 18, 66, y + block_height + 8), fill=(250, 204, 21, 255))
    for line in lines:
        draw.text(
            (88, y),
            line,
            font=title_font,
            fill=(255, 255, 255, 255),
            stroke_width=6,
            stroke_fill=(0, 0, 0, 235),
        )
        y += line_height

    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(destination, format="JPEG", quality=94, optimize=True)
    return destination


def generate_viral_thumbnail(
    source: str,
    candidate: ClipCandidate,
    destination: Path,
) -> Path:
    frame_path = destination.with_name(f"{destination.stem}-frame.jpg")
    command = [
        ffmpeg_bin(),
        "-y",
        "-ss",
        f"{thumbnail_timestamp(candidate):.3f}",
        "-i",
        source,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(frame_path),
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0 or not frame_path.exists():
            raise RuntimeError(result.stderr[-800:] or "Could not extract thumbnail frame.")
        return render_thumbnail_from_frame(frame_path, destination, candidate)
    finally:
        if frame_path.exists():
            frame_path.unlink()
