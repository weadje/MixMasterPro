"""Re-render only the EN dub variant with piper Ryan VO.

Reuses the existing silent video master, music bed, and SFX track that
the previous run produced. Only the VO is regenerated (now non-silent),
then the EN audio is re-mixed and re-muxed onto the same visual cut.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib import audio as audio_mod
from lib import vo_elevenlabs as vo_mod
import yaml

PROJECT = ROOT
WORK = PROJECT / "work"
RENDERS = PROJECT / "renders"


def main():
    tl = yaml.safe_load((PROJECT / "timeline.yaml").read_text())
    duration = float(tl.get("duration_sec", 60.0))

    # 1) Generate EN VO lines via piper (Ryan)
    en_lines = [
        vo_mod.VoLine(scene=ln["scene"], t=float(ln["t"]),
                      copy=ln["copy"], lang="en")
        for ln in tl["vo_lines_en"]
    ]
    vo_dir = PROJECT / "assets" / "vo" / "lines"
    paths = vo_mod.synthesize_all(en_lines, vo_dir)
    vo_cues = [audio_mod.VoCue(t=ln.t, wav_path=p) for ln, p in zip(en_lines, paths)]

    # 2) Build the assembled VO track (each line at its YAML t)
    vo_track = audio_mod.build_vo_track(
        vo_cues, WORK / "vo_master_en.wav",
        total_duration=duration,
    )
    print(f"VO track ready: {vo_track}")

    # 3) Re-mix master audio EN: music + ducked VO + SFX → loudnorm
    music = WORK / "music_bed.wav"
    sfx = WORK / "sfx_master.wav"
    if not (music.exists() and sfx.exists()):
        print("Missing music/sfx work files; run full pipeline first.")
        sys.exit(1)

    master_audio = audio_mod.mix_master(
        music_path=music, vo_path=vo_track, sfx_path=sfx,
        out_path=WORK / "master_audio_en.wav",
        duck_db=tl.get("global_rules", {}).get("vo_ducks_music_db", -6),
        total_duration=duration,
    )
    aac = audio_mod.encode_aac(master_audio, WORK / "master_audio_en.m4a")
    print(f"Master audio ready: {aac}")

    # 4) Re-mux EN master onto the existing silent visual
    silent_video = WORK / "shots" / "video_silent_master.mp4"
    if not silent_video.exists():
        print("Missing silent visual master; run full pipeline first.")
        sys.exit(1)

    out_mp4 = RENDERS / "mixmaster_lakay_9x16_master_en.mp4"
    import subprocess
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(silent_video), "-i", str(aac),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart",
        str(out_mp4),
    ], check=True)
    print(f"✓ {out_mp4}")


if __name__ == "__main__":
    main()
