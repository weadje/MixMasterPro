"""Audio assembly + master.

Layers (in order, all rendered to 48k stereo before mixing):
  1) Music bed (Kompa, 60s, builds in 3 stages).
  2) VO (per-line WAVs assembled into a 60s timeline by silence-padding to YAML t).
  3) SFX cues at exact timestamps (loops support `until` end times).
  4) Vinyl crackle global layer during scenes 1, 4, 7.

Mixing:
  * VO is mixed first, then sidechained against music to duck music -6 dB
    with 200ms attack / 400ms release.
  * SFX layer is summed in independently.
  * Final master goes through `loudnorm` (two-pass-style single-pass) targeting
    I=-14 LUFS, TP=-1 dBTP, LRA=11 (broadcast-style).
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .placeholders import ensure_asset, make_silent_wav


SAMPLE_RATE = 48000
DURATION = 60.0


@dataclass
class SfxCue:
    t: float
    file_rel: str           # e.g. "assets/sfx/radio_static.wav"
    gain_db: float = -12.0
    fade_in_ms: int = 0
    loop: bool = False
    until: float | None = None


@dataclass
class VoCue:
    t: float
    wav_path: Path           # absolute path to the per-line WAV
    gain_db: float = 0.0


def _gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def build_vo_track(vo_cues: list[VoCue], out_path: Path,
                   *, total_duration: float = DURATION,
                   sample_rate: int = SAMPLE_RATE) -> Path:
    """Place each VO line at its t (seconds) into a single 60s mono WAV.

    Uses ffmpeg `adelay` per input, then `amix` with sum=1 (don't auto-divide).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not vo_cues:
        return make_silent_wav(out_path, total_duration, sample_rate=sample_rate)

    inputs: list[str] = []
    filters: list[str] = []
    mix_inputs: list[str] = []

    for i, cue in enumerate(vo_cues):
        inputs += ["-i", str(cue.wav_path)]
        delay_ms = int(round(cue.t * 1000))
        gain = _gain(cue.gain_db)
        # Convert to stereo so amix is consistent across the chain.
        filters.append(
            f"[{i}:a]aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms},volume={gain:.3f}[v{i}]"
        )
        mix_inputs.append(f"[v{i}]")

    # Add a base silence track to lock the duration.
    silence = out_path.with_suffix(".silence.wav")
    make_silent_wav(silence, total_duration, sample_rate=sample_rate)
    inputs += ["-i", str(silence)]
    silence_idx = len(vo_cues)
    filters.append(f"[{silence_idx}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[base]")
    mix_inputs.append("[base]")

    n = len(mix_inputs)
    filters.append(f"{''.join(mix_inputs)}amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0[out]")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-ar", str(sample_rate), "-ac", "2",
        "-c:a", "pcm_s24le",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    silence.unlink(missing_ok=True)
    return out_path


def build_sfx_track(cues: list[SfxCue], out_path: Path,
                    *, project_root: Path,
                    total_duration: float = DURATION,
                    sample_rate: int = SAMPLE_RATE) -> Path:
    """Layer SFX cues at their exact timestamps; honors loop+until."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not cues:
        return make_silent_wav(out_path, total_duration, sample_rate=sample_rate)

    inputs: list[str] = []
    filters: list[str] = []
    mix_inputs: list[str] = []

    for i, c in enumerate(cues):
        wav_path, _ = ensure_asset(c.file_rel, project_root, duration_sec=2.0)
        if c.loop:
            inputs += ["-stream_loop", "-1", "-i", str(wav_path)]
        else:
            inputs += ["-i", str(wav_path)]

        delay_ms = int(round(c.t * 1000))
        gain = _gain(c.gain_db)
        chain_parts = [
            f"aresample={sample_rate}",
            "aformat=sample_fmts=fltp:channel_layouts=stereo",
        ]
        if c.loop and c.until is not None:
            dur = max(0.05, c.until - c.t)
            chain_parts.append(f"atrim=duration={dur:.3f}")
        if c.fade_in_ms > 0:
            chain_parts.append(f"afade=t=in:st=0:d={c.fade_in_ms/1000.0:.3f}")
        chain_parts += [
            f"adelay={delay_ms}|{delay_ms}",
            f"volume={gain:.3f}",
        ]
        filters.append(f"[{i}:a]{','.join(chain_parts)}[s{i}]")
        mix_inputs.append(f"[s{i}]")

    silence = out_path.with_suffix(".silence.wav")
    make_silent_wav(silence, total_duration, sample_rate=sample_rate)
    inputs += ["-i", str(silence)]
    base_idx = len(cues)
    filters.append(f"[{base_idx}:a]aformat=sample_fmts=fltp:channel_layouts=stereo[base]")
    mix_inputs.append("[base]")

    n = len(mix_inputs)
    filters.append(f"{''.join(mix_inputs)}amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0[out]")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-t", f"{total_duration:.3f}",
        "-ar", str(sample_rate), "-ac", "2",
        "-c:a", "pcm_s24le",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    silence.unlink(missing_ok=True)
    return out_path


def prepare_music_bed(music_rel: str, project_root: Path, out_path: Path,
                      *, total_duration: float = DURATION,
                      sample_rate: int = SAMPLE_RATE) -> Path:
    """Trim/loop the music bed to exactly total_duration."""
    music_path, _ = ensure_asset(music_rel, project_root,
                                 duration_sec=total_duration + 2.0)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-stream_loop", "-1", "-i", str(music_path),
        "-t", f"{total_duration:.3f}",
        "-af", f"aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo",
        "-ar", str(sample_rate), "-ac", "2",
        "-c:a", "pcm_s24le",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path


def mix_master(
    *,
    music_path: Path,
    vo_path: Path,
    sfx_path: Path,
    out_path: Path,
    duck_db: float = -6.0,
    total_duration: float = DURATION,
    sample_rate: int = SAMPLE_RATE,
    target_lufs: float = -14.0,
    true_peak_dbtp: float = -1.0,
) -> Path:
    """Sidechain-duck the music with VO, sum SFX, then loudnorm to spec."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Sidechain compression: VO triggers gain reduction on music.
    # 200ms attack / 400ms release. ratio ~6:1, threshold around -22dB.
    # ducking depth is governed by `duck_db` via makeup adjustment after compressor.
    duck_makeup = max(0.0, 0.0)  # we apply post-compression volume cut to ensure -6 dB net duck.
    fc = (
        f"[0:a]aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo[m];"
        f"[1:a]aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo[v];"
        f"[2:a]aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=stereo[s];"
        f"[v]asplit=2[v_out][v_sc];"
        f"[m][v_sc]sidechaincompress=threshold=0.05:ratio=6:attack=200:release=400:makeup=1[mducked];"
        f"[mducked]volume={_gain(duck_db):.3f}[mducked2];"
        f"[mducked2][v_out][s]amix=inputs=3:duration=longest:dropout_transition=0:normalize=0,"
        f"loudnorm=I={target_lufs}:LRA=11:TP={true_peak_dbtp}[mix]"
    )

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(music_path),
        "-i", str(vo_path),
        "-i", str(sfx_path),
        "-filter_complex", fc,
        "-map", "[mix]",
        "-t", f"{total_duration:.3f}",
        "-ar", str(sample_rate), "-ac", "2",
        "-c:a", "pcm_s24le",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path


def encode_aac(in_wav: Path, out_aac: Path,
               bitrate: str = "320k", sample_rate: int = SAMPLE_RATE) -> Path:
    out_aac = Path(out_aac)
    out_aac.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(in_wav),
        "-c:a", "aac", "-b:a", bitrate, "-ar", str(sample_rate), "-ac", "2",
        str(out_aac),
    ]
    subprocess.run(cmd, check=True)
    return out_aac
