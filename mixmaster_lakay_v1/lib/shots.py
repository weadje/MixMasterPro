"""Shot rendering: turn each YAML scene into a single MP4 segment with motion.

Every shot:
  * obeys global_rules.no_static_frames (zoompan or pan applied)
  * is rendered to the master 1080x1920 canvas
  * has a stable duration snapped to the 92 BPM grid by the orchestrator
  * receives the warm color bias when warm_color_bias is true

Shots come in two flavors:
  1) source-backed (real footage / UI capture / hero MOV) — we crop+scale+zoom
  2) synthetic (solid, kinetic_typography, riser_visual, end_card, etc.) — we
     compose a card with overlays, then push through the same motion path.
"""

from __future__ import annotations

import math
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .placeholders import (
    DESIGN_COLORS,
    _font,
    _hex_to_rgb,
    ensure_asset,
    make_placeholder_card,
    make_placeholder_video,
)
from PIL import Image, ImageDraw, ImageFilter


@dataclass
class Shot:
    scene_id: str
    index: int
    t_start: float
    t_end: float
    spec: dict
    out_path: Path

    @property
    def duration(self) -> float:
        return max(0.05, self.t_end - self.t_start)


def _ffprobe_duration(p: Path) -> float | None:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                str(p),
            ]
        )
        return float(out.decode().strip())
    except Exception:
        return None


def _warm_grade_filter(strength: float = 1.0) -> str:
    # Contrast +4, saturation +6, warm tint amber, vignette 18%.
    return (
        "eq=contrast=1.04:saturation=1.06,"
        "colorbalance=rs=0.05:gs=0.02:bs=-0.06:rm=0.04:gm=0.01:bm=-0.05:rh=0.03:gh=0.01:bh=-0.04,"
        "vignette=PI/5"
    )


def _zoompan_filter(duration_sec: float, fps: int, w: int, h: int,
                    z_start: float = 1.00, z_end: float = 1.06) -> str:
    """Smooth, monotonic zoom from z_start -> z_end across `duration_sec`.

    Pre-upscale to ~2x output size before zoompan so the max zoom (1.06x) still
    sources from a frame larger than output, avoiding pixel-doubling. We avoid
    the common `scale=8000:-1` recipe — at 1080p output it costs ~10x runtime
    for no visible improvement.
    """
    frames = max(1, int(round(duration_sec * fps)))
    span = max(0.0001, z_end - z_start)
    z_expr = f"{z_start}+({span})*on/{frames}"
    pre = max(w, h) * 2  # 2x master is plenty for 1.06x zoom
    return (
        f"scale={pre}:-2:flags=lanczos,"
        f"zoompan=z='{z_expr}':d={frames}:s={w}x{h}:fps={fps}"
    )


def _crop_to_aspect(w: int, h: int) -> str:
    # Center-crop input to 9:16 before scaling, preserving the frame.
    # in_w/in_h evaluated at filter graph time.
    return f"crop='if(gt(iw/ih,{w}/{h}),ih*{w}/{h},iw)':'if(gt(iw/ih,{w}/{h}),ih,iw*{h}/{w})'"


def _kinetic_typography_card(
    out_png: Path,
    copy: str,
    *,
    size: tuple[int, int],
    color_hex: str,
    font_size: int,
) -> Path:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    img = Image.new("RGB", (w, h), _hex_to_rgb(DESIGN_COLORS["bg_black"]))
    draw = ImageDraw.Draw(img)
    font = _font(font_size)

    # Word stagger via line breaks every ~14 chars on word boundaries.
    words = copy.split()
    lines: list[str] = []
    cur = ""
    for w_ in words:
        if len(cur) + len(w_) + 1 > 14 and cur:
            lines.append(cur.strip())
            cur = w_
        else:
            cur = (cur + " " + w_).strip()
    if cur:
        lines.append(cur)

    # Vertical center
    line_h = font_size + 12
    total_h = line_h * len(lines)
    y = (h - total_h) // 2
    fill = _hex_to_rgb(color_hex)
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) / 2, y), ln, fill=fill, font=font)
        y += line_h

    # Soft glow border
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    img.save(out_png, "PNG")
    return out_png


def _end_card(out_png: Path, layout: dict, *, size: tuple[int, int]) -> Path:
    w, h = size
    img = Image.new("RGB", (w, h), _hex_to_rgb(DESIGN_COLORS["bg_black"]))
    draw = ImageDraw.Draw(img)

    title_font = _font(96)
    pillar_font = _font(56)
    foot_font = _font(36)

    title = "MIXMASTER PRO"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) / 2, h * 0.30), title, fill=_hex_to_rgb(DESIGN_COLORS["gold"]), font=title_font)

    sub = "TANDE LAKAY OU"
    sub_font = _font(56)
    bbox = draw.textbbox((0, 0), sub, font=sub_font)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) / 2, h * 0.30 + 130), sub, fill=_hex_to_rgb(DESIGN_COLORS["text_primary"]), font=sub_font)

    pillars = layout.get("pillars", [])
    pillar_color = _hex_to_rgb(DESIGN_COLORS.get(layout.get("pillars_color", "gold"), "#E8B84A"))
    py = int(h * 0.55)
    for p in pillars:
        bbox = draw.textbbox((0, 0), p, font=pillar_font)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) / 2, py), p, fill=pillar_color, font=pillar_font)
        py += 90

    footer = layout.get("footer", "")
    if footer:
        bbox = draw.textbbox((0, 0), footer, font=foot_font)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) / 2, h - 200), footer, fill=_hex_to_rgb(DESIGN_COLORS["text_secondary"]), font=foot_font)

    img.save(out_png, "PNG")
    return out_png


def _solid_card(out_png: Path, color_hex: str, *, size: tuple[int, int]) -> Path:
    w, h = size
    Image.new("RGB", (w, h), _hex_to_rgb(color_hex)).save(out_png, "PNG")
    return out_png


def _riser_card(out_png: Path, color_hex: str, *, size: tuple[int, int]) -> Path:
    w, h = size
    img = Image.new("RGB", (w, h), _hex_to_rgb(DESIGN_COLORS["bg_black"]))
    draw = ImageDraw.Draw(img)
    glow = _hex_to_rgb(color_hex)
    for r in range(min(w, h) // 2, 0, -20):
        a = max(0, 180 - int(180 * r / (min(w, h) / 2)))
        rgba = (glow[0], glow[1], glow[2], a)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.ellipse((w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r), fill=rgba)
        img.paste(layer, (0, 0), layer)
    img.save(out_png, "PNG")
    return out_png


def _hud_label_filter(text: str, color_hex: str, position: str = "top",
                      h_canvas: int = 1920) -> str:
    # Use drawtext positioned in safe-zone (avoid top 220px for 9:16 TikTok UI).
    y = "240" if position == "top" else "h-580"
    safe_text = text.replace(":", r"\:").replace("'", r"\\'")
    color = "0x" + color_hex.lstrip("#") + "@1.0"
    return (
        f"drawtext=text='{safe_text}':fontcolor={color}:fontsize=48:"
        f"box=1:boxcolor=0x000000@0.45:boxborderw=18:"
        f"x=(w-text_w)/2:y={y}"
    )


def _shot_synthetic_image(spec: dict, out_png: Path, size: tuple[int, int]) -> Path:
    stype = spec.get("type")
    if stype == "solid":
        return _solid_card(out_png, DESIGN_COLORS.get(spec.get("color", "bg_black"), "#03050A"), size=size)
    if stype == "kinetic_typography":
        color = DESIGN_COLORS.get(spec.get("color", "text_primary"), "#FFFFFF")
        return _kinetic_typography_card(
            out_png, spec.get("copy", ""), size=size,
            color_hex=color, font_size=int(spec.get("size", 84)),
        )
    if stype == "riser_visual":
        return _riser_card(out_png, DESIGN_COLORS.get(spec.get("color", "gold"), "#E8B84A"), size=size)
    if stype == "end_card":
        return _end_card(out_png, spec.get("layout", {}), size=size)
    if stype == "signature_highlight":
        return _kinetic_typography_card(
            out_png, f"{spec.get('badge','SIGNATURE')}\n{spec.get('target','')}",
            size=size, color_hex=DESIGN_COLORS["gold"], font_size=72,
        )
    if stype in ("bg_montage_echoes", "hero_phone_preview"):
        return make_placeholder_card(
            out_png, spec.get("type", "shot").upper(),
            subtitle="composite layer",
            size=size,
        )
    # Fallback: generic placeholder card.
    return make_placeholder_card(out_png, stype or "SHOT", size=size)


def render_shot(
    shot: Shot,
    *,
    project_root: Path,
    fps: int,
    canvas: tuple[int, int],
    warm_grade: bool,
    text_overlays: list[dict] | None = None,
    log=print,
) -> Path:
    """Render one shot to MP4 at the master canvas size.

    text_overlays: list of {copy, color, position, t_rel_start, t_rel_end} relative to shot start.
    """
    spec = shot.spec
    cw, ch = canvas
    out = shot.out_path
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / "_work"
    work.mkdir(parents=True, exist_ok=True)

    duration = shot.duration
    src_rel = spec.get("src")

    # Resolve source: real file or generated synthetic card.
    input_args: list[str]
    if src_rel:
        src_path, was_ph = ensure_asset(
            src_rel, project_root,
            duration_sec=max(duration + 0.5, 2.0),
            fps=fps, size=(cw, ch),
            subtitle=shot.scene_id,
        )
        if was_ph:
            log(f"    · synthesized placeholder: {src_rel}")
        input_args = ["-stream_loop", "-1", "-i", str(src_path)]
        scale_chain = (
            f"{_crop_to_aspect(cw, ch)},scale={cw}:{ch}:flags=lanczos,setsar=1,fps={fps}"
        )
    else:
        # Synthetic card → still image fed through zoompan
        png = work / f"{shot.scene_id}_{shot.index:02d}.png"
        _shot_synthetic_image(spec, png, size=(cw, ch))
        input_args = ["-loop", "1", "-i", str(png)]
        scale_chain = f"scale={cw}:{ch}:flags=lanczos,setsar=1,fps={fps}"

    # Motion: explicit zoom range from spec, else default 1.00 -> 1.04.
    z_range = spec.get("zoom") or [1.00, 1.04]
    z_start, z_end = float(z_range[0]), float(z_range[1])
    if abs(z_end - z_start) < 0.02:
        z_end = z_start + 0.04  # enforce min_zoom_per_shot 1.02 worth of motion

    motion = _zoompan_filter(duration, fps, cw, ch, z_start=z_start, z_end=z_end)

    chain = [scale_chain, motion]
    if warm_grade:
        chain.append(_warm_grade_filter())

    # HUD callout
    hud = spec.get("hud")
    if hud:
        chain.append(_hud_label_filter(
            text=hud.get("copy", ""),
            color_hex=DESIGN_COLORS.get(hud.get("color", "gold"), "#E8B84A"),
            position=hud.get("position", "top"),
            h_canvas=ch,
        ))

    # Lower-third / accent text overlays (passed in from scene)
    for ov in text_overlays or []:
        text = (ov.get("copy") or "").replace(":", r"\:").replace("'", r"\\'")
        color = "0x" + DESIGN_COLORS.get(ov.get("color", "text_primary"), "#FFFFFF").lstrip("#")
        size = int(ov.get("size", 44))
        # Lower third sits at h - 580 to clear TikTok bottom UI safe zone.
        y = "h-620" if ov.get("position") == "lower_third" else "(h-text_h)/2"
        # enable= for time windows relative to shot start
        ts = ov.get("t_rel_start", 0.0)
        te = ov.get("t_rel_end", duration)
        chain.append(
            f"drawtext=text='{text}':fontcolor={color}@1.0:fontsize={size}:"
            f"box=1:boxcolor=0x000000@0.55:boxborderw=20:"
            f"x=(w-text_w)/2:y={y}:enable='between(t,{ts:.3f},{te:.3f})'"
        )

    chain.append("format=yuv420p")
    vf = ",".join(chain)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        *input_args,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def concat_shots(shots: list[Path], out_path: Path) -> Path:
    """Concat shot MP4s with the FFmpeg concat demuxer (re-encode for safety)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = out_path.with_suffix(".concat.txt")
    list_file.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in shots) + "\n",
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    list_file.unlink(missing_ok=True)
    return out_path
