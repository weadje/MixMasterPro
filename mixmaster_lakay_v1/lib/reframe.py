"""Aspect-ratio reframing.

Inputs are 9:16 1080x1920 master MP4s. We:
  * 1:1 (1080x1080) — center crop the middle square
  * 16:9 (1920x1080) — landscape canvas with blurred pillarbox of the source

Both reframes copy the master audio stream as-is so loudness stays compliant.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def to_square_1080(in_mp4: Path, out_mp4: Path) -> Path:
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    # 9:16 source (1080x1920) → center-square crop = iw x iw, vertically centered.
    vf = "crop=iw:iw:0:(ih-iw)/2,scale=1080:1080:flags=lanczos,setsar=1,format=yuv420p"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(in_mp4),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True)
    return out_mp4


def to_landscape_1920x1080(in_mp4: Path, out_mp4: Path) -> Path:
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    # Two paths from the same source:
    #  - blurred zoom-cover background (fills 1920x1080)
    #  - foreground scaled to 608x1080 (preserves 9:16) and centered
    fc = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1920:-1:flags=lanczos,crop=1920:1080,boxblur=40:2,eq=brightness=-0.05[bgb];"
        "[fg]scale=-1:1080:flags=lanczos,setsar=1[fg2];"
        "[bgb][fg2]overlay=(W-w)/2:0,format=yuv420p[v]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(in_mp4),
        "-filter_complex", fc,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True)
    return out_mp4
