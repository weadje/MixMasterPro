"""Procedural music + SFX synthesis using FFmpeg's lavfi.

Real Kompa music isn't something we can synthesize from scratch — what we can
do is generate a 92-BPM-locked sketch that *sounds like the spot has music*:
a kick on beats 1+3, a soft hat on offbeats, a rolling bass, a chord pad.
It's a placeholder track, but it's a musical placeholder, not silence.

All SFX are also synthesized with lavfi sources so the mix has actual content
when the project is run without real assets.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

SR = 48000


def _run(args: list[str]) -> None:
    subprocess.run(args, check=True)


# ─────────────────────────────────────────────────────────── Music bed ────

def synth_kompa_bed(out_path: Path, duration: float = 60.0, bpm: float = 92.0) -> Path:
    """Layer a kick, hat, bass, and pad onto a single mixed WAV.

    Stages:
      0-16s   : warm pad only (intimate intro)
      16-34s  : pad + soft bass (promise lands)
      34-60s  : pad + bass + kick + hat (full Kompa groove)
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    beat = 60.0 / bpm

    # Build layers conditionally — only include each layer if there's room for it.
    pad = (
        f"sine=frequency=110:duration={duration}:sample_rate={SR}[p1];"
        f"sine=frequency=164.81:duration={duration}:sample_rate={SR}[p2];"
        f"sine=frequency=246.94:duration={duration}:sample_rate={SR}[p3];"
        f"[p1][p2][p3]amix=inputs=3:normalize=0,"
        f"lowpass=f=1200,"
        f"tremolo=f=0.5:d=0.15,"
        f"volume=0.18,"
        f"afade=t=in:st=0:d={min(2.0, duration/4):.3f},"
        f"afade=t=out:st={max(0.1, duration-2):.3f}:d={min(2.0, duration/4):.3f}[pad]"
    )
    layers = [("pad", pad)]

    bass_start = 16.0
    if duration > bass_start + 1.0:
        bass_dur = duration - bass_start
        bass = (
            f"sine=frequency=65.41:duration={bass_dur}:sample_rate={SR},"
            f"adelay={int(bass_start*1000)}|{int(bass_start*1000)},"
            f"tremolo=f={bpm/60.0}:d=0.95,"
            f"lowpass=f=200,"
            f"afade=t=in:st={bass_start}:d=2.0,"
            f"afade=t=out:st={duration-1.0:.3f}:d=1.0,"
            f"volume=0.4[bass]"
        )
        layers.append(("bass", bass))

    drums_start = 34.0
    if duration > drums_start + 1.0:
        drum_dur = duration - drums_start
        kick = (
            f"sine=frequency=55:duration={drum_dur}:sample_rate={SR},"
            f"adelay={int(drums_start*1000)}|{int(drums_start*1000)},"
            f"tremolo=f={bpm/60.0/2.0}:d=0.99,"
            f"lowpass=f=120,"
            f"volume=0.55[kick]"
        )
        hat = (
            f"anoisesrc=color=white:duration={drum_dur}:sample_rate={SR}:amplitude=0.6,"
            f"adelay={int(drums_start*1000)}|{int(drums_start*1000)},"
            f"highpass=f=6000,"
            f"tremolo=f={bpm/60.0*2.0}:d=0.95,"
            f"volume=0.10[hat]"
        )
        layers += [("kick", kick), ("hat", hat)]

    layer_chains = ";".join(c for _, c in layers)
    mix_inputs = "".join(f"[{n}]" for n, _ in layers)
    fc = (
        f"{layer_chains};"
        f"{mix_inputs}amix=inputs={len(layers)}:normalize=0:duration=longest,"
        f"alimiter=limit=0.95,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-filter_complex", fc, "-map", "[out]",
        "-t", f"{duration:.3f}",
        "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s24le",
        str(out_path),
    ]
    _run(cmd)
    return out_path


# ─────────────────────────────────────────────────────────────── SFX ─────

def _synth_to_wav(filter_complex: str, out_path: Path, duration: float) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-filter_complex", filter_complex, "-map", "[out]",
        "-t", f"{duration:.3f}",
        "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s24le",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def synth_radio_static(out_path: Path) -> Path:
    fc = (
        f"anoisesrc=color=white:duration=3:sample_rate={SR}:amplitude=0.7,"
        f"highpass=f=400,lowpass=f=4000,"
        f"tremolo=f=12:d=0.4,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )
    return _synth_to_wav(fc, out_path, 3.0)


def synth_radio_tune_sweep(out_path: Path) -> Path:
    """A frequency sweep simulating tuning across stations."""
    fc = (
        f"anoisesrc=color=pink:duration=2:sample_rate={SR}:amplitude=0.5[n];"
        f"sine=frequency=300:beep_factor=2:duration=2:sample_rate={SR}[s];"
        f"[s][n]amix=inputs=2:normalize=0,"
        f"firequalizer=gain='if(lt(f,300),-30,if(gt(f,3000),-30,0))',"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )
    return _synth_to_wav(fc, out_path, 2.0)


def synth_vinyl_crackle_loop(out_path: Path) -> Path:
    """Sparse crackle — short noise transients on a near-silent bed."""
    fc = (
        f"anoisesrc=color=pink:duration=4:sample_rate={SR}:amplitude=0.18,"
        f"highpass=f=2000,lowpass=f=8000,"
        f"tremolo=f=23:d=0.85,"
        f"volume=0.25,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )
    return _synth_to_wav(fc, out_path, 4.0)


def synth_rain_window_loop(out_path: Path) -> Path:
    fc = (
        f"anoisesrc=color=pink:duration=6:sample_rate={SR}:amplitude=0.5,"
        f"highpass=f=200,lowpass=f=2500,"
        f"volume=0.35,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )
    return _synth_to_wav(fc, out_path, 6.0)


def synth_crowd_ambience(out_path: Path) -> Path:
    fc = (
        f"anoisesrc=color=brown:duration=8:sample_rate={SR}:amplitude=0.45,"
        f"highpass=f=120,lowpass=f=900,"
        f"tremolo=f=0.3:d=0.4,"
        f"volume=0.4,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )
    return _synth_to_wav(fc, out_path, 8.0)


def synth_ui_tap(out_path: Path) -> Path:
    fc = (
        f"sine=frequency=880:duration=0.06:sample_rate={SR},"
        f"afade=t=out:st=0:d=0.06,"
        f"volume=0.6,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )
    return _synth_to_wav(fc, out_path, 0.1)


def synth_riser(out_path: Path) -> Path:
    fc = (
        f"anoisesrc=color=white:duration=2:sample_rate={SR}:amplitude=0.7[n];"
        f"sine=frequency=200:duration=2:sample_rate={SR}[s];"
        f"[n][s]amix=inputs=2:normalize=0,"
        f"highpass=f=300,"
        f"afade=t=in:st=0:d=2.0,"
        f"volume=0.7,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )
    return _synth_to_wav(fc, out_path, 2.0)


def synth_preset_impact(out_path: Path) -> Path:
    fc = (
        f"sine=frequency=180:duration=0.4:sample_rate={SR},"
        f"afade=t=out:st=0.05:d=0.35,"
        f"lowpass=f=600,"
        f"volume=0.7,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )
    return _synth_to_wav(fc, out_path, 0.5)


def synth_shimmer_gold(out_path: Path) -> Path:
    fc = (
        f"sine=frequency=2200:duration=1.2:sample_rate={SR}[s1];"
        f"sine=frequency=3300:duration=1.2:sample_rate={SR}[s2];"
        f"[s1][s2]amix=inputs=2:normalize=0,"
        f"afade=t=in:st=0:d=0.1,afade=t=out:st=0.3:d=0.9,"
        f"volume=0.35,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )
    return _synth_to_wav(fc, out_path, 1.3)


def synth_confirm_chime(out_path: Path) -> Path:
    fc = (
        f"sine=frequency=880:duration=0.5:sample_rate={SR}[s1];"
        f"sine=frequency=1320:duration=0.5:sample_rate={SR}[s2];"
        f"[s2]adelay=120|120[s2d];"
        f"[s1][s2d]amix=inputs=2:normalize=0,"
        f"afade=t=out:st=0.3:d=0.5,"
        f"volume=0.55,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates={SR}[out]"
    )
    return _synth_to_wav(fc, out_path, 1.0)


SFX_RENDERERS = {
    "sfx/radio_static":         synth_radio_static,
    "sfx/radio_tune_sweep":     synth_radio_tune_sweep,
    "sfx/vinyl_crackle_loop":   synth_vinyl_crackle_loop,
    "sfx/rain_window_loop":     synth_rain_window_loop,
    "sfx/crowd_ambience_warm":  synth_crowd_ambience,
    "sfx/ui_tap_soft":          synth_ui_tap,
    "sfx/riser_emotional":      synth_riser,
    "sfx/preset_impact_warm":   synth_preset_impact,
    "sfx/shimmer_gold":         synth_shimmer_gold,
    "sfx/confirm_chime":        synth_confirm_chime,
    "music/bed_kompa_emotional_92bpm": lambda p: synth_kompa_bed(p, duration=60.0, bpm=92.0),
}


def find_audio_renderer(relpath: str):
    key = relpath.replace("\\", "/")
    if key.startswith("assets/"):
        key = key[len("assets/"):]
    if "." in key.rsplit("/", 1)[-1]:
        key = key.rsplit(".", 1)[0]
    return SFX_RENDERERS.get(key)
