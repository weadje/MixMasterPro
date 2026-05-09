"""Hook variant overlays for Scene 1 (0-6s).

Each hook produces a 6-second 9:16 MP4 covering the cinematic open. They share
the same VO/audio mix when assembled in render.py — only the visuals change.

  Hook A: rain on window + radio static reveal (default)
  Hook B: black screen → tuning dial sweep across frequencies
  Hook C: vinyl needle drop close-up → app glow reveal
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .placeholders import (
    DESIGN_COLORS,
    ensure_asset,
    make_placeholder_card,
)
from PIL import Image, ImageDraw, ImageFilter
from .placeholders import _hex_to_rgb, _font


HOOK_DURATION = 6.0  # seconds


def _write_card(path: Path, label: str, sub: str, color_hex: str,
                size: tuple[int, int] = (1080, 1920)) -> Path:
    w, h = size
    img = Image.new("RGB", (w, h), _hex_to_rgb(DESIGN_COLORS["bg_black"]))
    draw = ImageDraw.Draw(img)
    accent = _hex_to_rgb(color_hex)

    # Glow
    glow = Image.new("RGB", (w, h), accent)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    cx, cy = w // 2, h // 2
    for r in range(min(w, h) // 2, 0, -30):
        a = max(0, 90 - int(90 * r / (min(w, h) / 2)))
        md.ellipse((cx - r, cy - r, cx + r, cy + r), fill=a)
    img.paste(glow, (0, 0), mask)

    title = _font(108)
    sub_font = _font(40)
    bbox = draw.textbbox((0, 0), label, font=title)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) / 2, h // 2 - 80), label,
              fill=_hex_to_rgb(DESIGN_COLORS["text_primary"]), font=title)
    bbox = draw.textbbox((0, 0), sub, font=sub_font)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) / 2, h // 2 + 60), sub,
              fill=_hex_to_rgb(DESIGN_COLORS["text_secondary"]), font=sub_font)
    img.save(path, "PNG")
    return path


def render_hook(
    hook_id: str,
    *,
    out_path: Path,
    project_root: Path,
    fps: int = 30,
    canvas: tuple[int, int] = (1080, 1920),
) -> Path:
    """Render the visual layer of one hook variant (no audio)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cw, ch = canvas
    work = out_path.parent / "_work"
    work.mkdir(parents=True, exist_ok=True)

    if hook_id == "A":
        src_rel = "assets/bnb/rain_on_window.mp4"
        src_path, _ = ensure_asset(src_rel, project_root,
                                   duration_sec=HOOK_DURATION + 1.0,
                                   fps=fps, size=(cw, ch),
                                   subtitle="HOOK A — rain on window")
        # Static intensity ramp (radio static visual = grain) + slow zoom.
        vf = (
            f"crop='if(gt(iw/ih,{cw}/{ch}),ih*{cw}/{ch},iw)':'if(gt(iw/ih,{cw}/{ch}),ih,iw*{ch}/{cw})',"
            f"scale={cw*2}:-2:flags=lanczos,setsar=1,fps={fps},"
            f"zoompan=z='1.00+0.06*on/{int(HOOK_DURATION*fps)}':d={int(HOOK_DURATION*fps)}:s={cw}x{ch}:fps={fps},"
            f"noise=alls=14:allf=t+u,"
            f"eq=contrast=1.04:saturation=0.85,"
            f"colorbalance=rs=-0.05:bs=0.10,"
            f"format=yuv420p"
        )
        inputs = ["-stream_loop", "-1", "-i", str(src_path)]

    elif hook_id == "B":
        # Black -> tuning dial sweep. Built from a card + horizontal pan + sweep gradient.
        card = work / "hook_B_dial.png"
        _write_card(card, "TUNING…", "92.7  →  104.5  →  98.1  FM", DESIGN_COLORS["neon_cyan"], size=(cw, ch))
        vf = (
            f"scale={cw*2}:-2:flags=lanczos,setsar=1,fps={fps},"
            f"zoompan=z='1.02+0.04*on/{int(HOOK_DURATION*fps)}':d={int(HOOK_DURATION*fps)}:s={cw}x{ch}:fps={fps},"
            f"noise=alls=8:allf=t,"
            f"format=yuv420p"
        )
        inputs = ["-loop", "1", "-i", str(card)]

    elif hook_id == "C":
        src_rel = "assets/bnb/vinyl_needle_drop.mp4"
        src_path, _ = ensure_asset(src_rel, project_root,
                                   duration_sec=HOOK_DURATION + 1.0,
                                   fps=fps, size=(cw, ch),
                                   subtitle="HOOK C — vinyl needle drop")
        vf = (
            f"crop='if(gt(iw/ih,{cw}/{ch}),ih*{cw}/{ch},iw)':'if(gt(iw/ih,{cw}/{ch}),ih,iw*{ch}/{cw})',"
            f"scale={cw*2}:-2:flags=lanczos,setsar=1,fps={fps},"
            f"zoompan=z='1.00+0.06*on/{int(HOOK_DURATION*fps)}':d={int(HOOK_DURATION*fps)}:s={cw}x{ch}:fps={fps},"
            f"eq=contrast=1.06:saturation=1.10,"
            f"colorbalance=rm=0.05:gm=0.02:bm=-0.04,"
            f"vignette=PI/5,"
            f"format=yuv420p"
        )
        inputs = ["-stream_loop", "-1", "-i", str(src_path)]

    else:
        raise ValueError(f"Unknown hook id: {hook_id}")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs,
        "-t", f"{HOOK_DURATION:.3f}",
        "-vf", vf,
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path
