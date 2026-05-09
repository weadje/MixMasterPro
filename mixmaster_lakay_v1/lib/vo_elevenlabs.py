"""ElevenLabs VO generation, per line, language-aware.

Critical rules:
  * Haitian Creole VO ALWAYS routes to ELEVENLABS_HT_VOICE_ID (a user-cloned voice).
    If that env var is not set, we WARN and fall back to silent placeholder lines.
    We never substitute Adam for Haitian Creole.
  * English (Adam) is hard-coded to voice_id pNInz6obpgDQGcFmaJgB.
  * Each line is generated as its own API call, saved to assets/vo/lines/<lang>_<scene>.wav,
    then post-processed and assembled by audio.py into a master VO track.
"""

from __future__ import annotations

import os
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

from .placeholders import make_silent_wav

ADAM_VOICE_ID = "pNInz6obpgDQGcFmaJgB"
EL_MODEL = "eleven_multilingual_v2"

VOICE_SETTINGS_HT = {
    "stability": 0.50,
    "similarity_boost": 0.85,
    "style": 0.40,
    "use_speaker_boost": True,
}
VOICE_SETTINGS_EN = {
    "stability": 0.45,
    "similarity_boost": 0.80,
    "style": 0.35,
    "use_speaker_boost": True,
}


@dataclass
class VoLine:
    scene: str
    t: float
    copy: str
    lang: str  # "ht" or "en"


def _resolve_voice(lang: str) -> tuple[str | None, dict, str]:
    if lang == "ht":
        vid = os.environ.get("ELEVENLABS_HT_VOICE_ID")
        return vid, VOICE_SETTINGS_HT, "Haitian Creole (cloned)"
    if lang == "en":
        return ADAM_VOICE_ID, VOICE_SETTINGS_EN, "Adam (English)"
    raise ValueError(f"Unknown VO lang: {lang}")


def _api_key() -> str | None:
    return os.environ.get("ELEVENLABS_API_KEY")


def _estimate_speech_duration(copy: str) -> float:
    # ~2.5 syllables/sec for emotional VO, fall back on word count.
    words = max(1, len(copy.split()))
    return max(1.0, min(8.0, words / 2.6))


def synthesize_line(line: VoLine, out_path: Path, *, log=print) -> Path:
    """Render one VO line to WAV. Falls back to silence on any failure."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    voice_id, settings, voice_label = _resolve_voice(line.lang)
    api_key = _api_key()

    if line.lang == "ht" and not voice_id:
        log(
            "  ⚠ ELEVENLABS_HT_VOICE_ID is not set. Haitian Creole VO will be SILENT.\n"
            "    Provide a cloned voice ID, or drop a recorded WAV at\n"
            "    assets/vo/vo_haitian_male_master.wav. Adam will NOT be substituted."
        )
        make_silent_wav(out_path, _estimate_speech_duration(line.copy))
        return out_path

    if not api_key or requests is None:
        log(f"  ⚠ ELEVENLABS_API_KEY missing — placeholder silence for {line.lang}/{line.scene}.")
        make_silent_wav(out_path, _estimate_speech_duration(line.copy))
        return out_path

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": line.copy,
        "model_id": EL_MODEL,
        "voice_settings": settings,
    }

    try:
        log(f"  → ElevenLabs [{voice_label}] {line.scene}: \"{line.copy[:60]}\"")
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        r.raise_for_status()
    except Exception as e:
        log(f"  ⚠ ElevenLabs request failed ({e}) — falling back to silence.")
        make_silent_wav(out_path, _estimate_speech_duration(line.copy))
        return out_path

    mp3_path = out_path.with_suffix(".mp3")
    mp3_path.write_bytes(r.content)

    # Convert MP3 → WAV 48k mono with light cleanup chain.
    import subprocess
    af = (
        "highpass=f=80,"
        "deesser,"
        "acompressor=threshold=-18dB:ratio=2.5:attack=10:release=120,"
        "loudnorm=I=-20:LRA=7:TP=-3,"
        "aresample=48000"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(mp3_path),
        "-af", af,
        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s24le",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        # Some ffmpeg builds lack `deesser`; retry without it.
        af2 = af.replace("deesser,", "")
        cmd[cmd.index(af)] = af2
        subprocess.run(cmd, check=True)
    finally:
        mp3_path.unlink(missing_ok=True)

    return out_path


def synthesize_all(lines: Iterable[VoLine], out_dir: Path, *, log=print) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for line in lines:
        p = out_dir / f"{line.lang}_{line.scene}.wav"
        synthesize_line(line, p, log=log)
        paths.append(p)
    return paths
