"""Placeholder asset generation.

If a real asset is missing from /assets/, the pipeline must not fail. Instead it
synthesizes a card / tone / clip in its place. Every generated placeholder is
clearly labeled "PLACEHOLDER" so it is impossible to ship one accidentally.
"""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

DESIGN_COLORS = {
    "bg_black":      "#03050A",
    "bg_panel":      "#0A0E18",
    "neon_cyan":     "#00E5FF",
    "neon_teal":     "#14E6C0",
    "neon_pink":     "#FF4FA3",
    "neon_violet":   "#A864FF",
    "deep_blue":     "#0B1F3A",
    "gold":          "#E8B84A",
    "warm_amber":    "#F2A15B",
    "text_primary":  "#FFFFFF",
    "text_secondary":"#B7C0CF",
}


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _stable_color(seed: str) -> tuple[int, int, int]:
    """Pick a deterministic neon-ish accent color from a seed string."""
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    palette = [
        "neon_cyan", "neon_teal", "neon_pink", "neon_violet",
        "gold", "warm_amber",
    ]
    pick = palette[int(digest[:2], 16) % len(palette)]
    return _hex_to_rgb(DESIGN_COLORS[pick])


def make_placeholder_card(
    out_path: Path,
    label: str,
    subtitle: str = "",
    size: tuple[int, int] = (1080, 1920),
) -> Path:
    """Render a labeled placeholder PNG card."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = size
    bg = _hex_to_rgb(DESIGN_COLORS["bg_panel"])
    accent = _stable_color(label)

    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)

    # Subtle radial gradient toward accent
    glow = Image.new("RGB", (w, h), accent)
    mask = Image.new("L", (w, h), 0)
    mdraw = ImageDraw.Draw(mask)
    cx, cy = w // 2, h // 2
    for r in range(min(w, h) // 2, 0, -40):
        alpha = max(0, 60 - int(60 * r / (min(w, h) / 2)))
        mdraw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=alpha)
    img.paste(glow, (0, 0), mask)

    # Diagonal stripes => unmistakable placeholder
    stripe = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(stripe)
    for i in range(-h, w + h, 80):
        sdraw.line([(i, 0), (i + h, h)], fill=(*accent, 28), width=22)
    img.paste(stripe, (0, 0), stripe)

    # Title
    title_font = _font(96 if w >= 1080 else 64)
    sub_font = _font(40 if w >= 1080 else 28)
    tag_font = _font(28)

    title_text = label
    bbox = draw.textbbox((0, 0), title_text, font=title_font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((w - tw) / 2, (h - th) / 2 - 60),
        title_text,
        fill=_hex_to_rgb(DESIGN_COLORS["text_primary"]),
        font=title_font,
    )

    if subtitle:
        bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
        sw = bbox[2] - bbox[0]
        draw.text(
            ((w - sw) / 2, (h - th) / 2 + 40),
            subtitle,
            fill=_hex_to_rgb(DESIGN_COLORS["text_secondary"]),
            font=sub_font,
        )

    tag = "PLACEHOLDER • mixmaster_lakay_v1"
    bbox = draw.textbbox((0, 0), tag, font=tag_font)
    tw = bbox[2] - bbox[0]
    draw.text(
        ((w - tw) / 2, h - 90),
        tag,
        fill=_hex_to_rgb(DESIGN_COLORS["gold"]),
        font=tag_font,
    )

    img.save(out_path, "PNG")
    return out_path


def make_placeholder_video(
    out_path: Path,
    label: str,
    duration_sec: float,
    fps: int = 30,
    size: tuple[int, int] = (1080, 1920),
    subtitle: str = "",
    *,
    relpath: str | None = None,
) -> Path:
    """Render a short MP4 with a slow zoom over the placeholder card.

    If a scene-specific compositor exists for `relpath` (e.g. a known UI capture
    or BnB shot), use that instead of the generic placeholder card so the result
    looks like the intended scene.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_png = out_path.with_suffix(".card.png")

    renderer = None
    if relpath:
        try:
            from .scene_visuals import find_renderer
            renderer = find_renderer(relpath)
        except Exception:
            renderer = None

    if renderer is not None:
        renderer(tmp_png, size=size)
    else:
        make_placeholder_card(tmp_png, label, subtitle=subtitle, size=size)

    # Slow Ken Burns: 1.00 -> 1.06 over duration. Even very short shots pass min_zoom.
    frames = max(1, int(round(duration_sec * fps)))
    zoom_expr = f"min(zoom+0.0006,1.06)"
    pre = max(size[0], size[1]) * 2
    vf = (
        f"scale={pre}:-2:flags=lanczos,"
        f"zoompan=z='{zoom_expr}':d={frames}:s={size[0]}x{size[1]}:fps={fps},"
        f"format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(tmp_png),
        "-t", f"{duration_sec:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    tmp_png.unlink(missing_ok=True)
    return out_path


def make_silent_wav(out_path: Path, duration_sec: float, sample_rate: int = 48000) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = int(duration_sec * sample_rate)
    with wave.open(str(out_path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n)
    return out_path


def make_tone_wav(
    out_path: Path,
    duration_sec: float,
    freq: float = 220.0,
    gain_db: float = -30.0,
    sample_rate: int = 48000,
) -> Path:
    """Synthesize a quiet sine — used in place of missing SFX so the mix never errors.

    Kept very low (-30 dB by default) so a missing SFX is audible but not jarring,
    and clearly identifiable in QC.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    amp = int(32767 * (10 ** (gain_db / 20.0)))
    n = int(duration_sec * sample_rate)
    with wave.open(str(out_path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        for i in range(n):
            v = int(amp * math.sin(2 * math.pi * freq * (i / sample_rate)))
            w.writeframes(v.to_bytes(2, "little", signed=True))
    return out_path


def make_placeholder_svg_logo(out_path: Path, label: str = "MIXMASTER PRO") -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 160">
  <rect width="600" height="160" fill="#0A0E18"/>
  <text x="300" y="100" text-anchor="middle"
        font-family="Inter, Arial, sans-serif" font-weight="800"
        font-size="56" fill="#E8B84A">{label}</text>
</svg>"""
    out_path.write_text(svg, encoding="utf-8")
    return out_path


def ensure_asset(
    relpath: str,
    project_root: Path,
    *,
    duration_sec: float = 4.0,
    fps: int = 30,
    size: tuple[int, int] = (1080, 1920),
    subtitle: str = "",
) -> tuple[Path, bool]:
    """Return (path, was_synthesized). Synthesizes a placeholder if missing.

    Routing by extension:
        .mp4 / .mov  -> placeholder video
        .wav         -> silent wav (or short tone for SFX hints)
        .svg         -> placeholder logo SVG
        .png / .jpg  -> placeholder card
    """
    # YAML shot specs use bare paths like "bnb/foo.mp4". asset_manifest uses
    # "assets/bnb/foo.mp4". Normalize so both resolve under assets/.
    norm = relpath.replace("\\", "/")
    if not norm.startswith("assets/"):
        norm = f"assets/{norm}"
    real = project_root / norm
    if real.exists() and real.stat().st_size > 0:
        return real, False

    real.parent.mkdir(parents=True, exist_ok=True)
    label = Path(relpath).stem.replace("_", " ").upper()
    ext = real.suffix.lower()

    if ext in (".mp4", ".mov"):
        make_placeholder_video(real, label, duration_sec, fps=fps, size=size,
                               subtitle=subtitle, relpath=norm)
    elif ext == ".wav":
        # Try synthesized audio (real Kompa-feel bed, real SFX). Fall back to silence.
        synth = None
        try:
            from .audio_synth import find_audio_renderer
            synth = find_audio_renderer(norm)
        except Exception:
            synth = None
        if synth is not None:
            try:
                synth(real)
            except Exception:
                make_silent_wav(real, max(0.5, duration_sec))
        elif "/sfx/" in norm:
            make_tone_wav(real, max(0.5, duration_sec), freq=440.0, gain_db=-32.0)
        else:
            make_silent_wav(real, max(0.5, duration_sec))
    elif ext == ".svg":
        make_placeholder_svg_logo(real, label=label)
    elif ext in (".png", ".jpg", ".jpeg"):
        make_placeholder_card(real, label, subtitle=subtitle, size=size)
    else:
        # Unknown extension — drop a card alongside.
        make_placeholder_card(real.with_suffix(".png"), label, subtitle=subtitle, size=size)

    return real, True


def audit_manifest(
    manifest: dict,
    project_root: Path,
) -> list[tuple[str, bool]]:
    """Return [(relpath, present)] for everything in asset_manifest."""
    out: list[tuple[str, bool]] = []
    for _category, items in (manifest or {}).items():
        for rel in items:
            out.append((rel, (project_root / rel).exists()))
    return out
