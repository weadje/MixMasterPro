#!/usr/bin/env python3
"""mixmaster_lakay_v1 — full render pipeline orchestrator.

Phases:
  1. Parse timeline.yaml → flat shot list snapped to the 92 BPM beat grid.
  2. Audit assets, synthesize placeholders for anything missing.
  3. Synthesize per-line VO via ElevenLabs (HT primary, EN dub for variant).
  4. Render each shot with motion → concat to silent 9:16 video.
  5. Build music bed, VO track, SFX track → sidechain-duck mix → loudnorm.
  6. Mux master 9:16 (HT). Render EN-dub variant (same visual cut, EN VO).
  7. Reframe to 1:1 and 16:9.
  8. Render three hook variants (A/B/C) for Scene 1.
  9. Generate SRT captions (HT and EN).
 10. Generate hero thumbnail at t=54s.

Run with: `python render.py` from the project root.
Optional flags:
  --skip-vo            Skip ElevenLabs calls (use silence)
  --hooks-only         Re-render hook variants only
  --bpm 100            Override BPM (rebuilds beat grid)
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib import audio as audio_mod
from lib import overlays as overlays_mod
from lib import placeholders as ph
from lib import reframe as reframe_mod
from lib import shots as shots_mod
from lib import vo_elevenlabs as vo_mod


# ---------------------------------------------------------------- helpers ----

def _log(msg: str = "") -> None:
    print(msg, flush=True)


def _snap_to_beat(t: float, beat_sec: float) -> float:
    return round(t / beat_sec) * beat_sec


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# ----------------------------------------------------------------- parse ----

@dataclass
class ParsedScene:
    id: str
    t_start: float
    t_end: float
    raw: dict
    shots: list[dict] = field(default_factory=list)
    text_overlays: list[dict] = field(default_factory=list)


def _normalize_scene_to_shots(scene: dict) -> list[dict]:
    """Each scene may have `layers`, `shots`, or `composite`. Return shot-spec list."""
    if "shots" in scene:
        return list(scene["shots"])
    if "layers" in scene:
        # For Scene 1 we use only the dominant on-screen layer per time band.
        # Build a coarse partition: pick the last layer that overlaps each band.
        layers = scene["layers"]
        # Convert overlapping layer list into sequential shots by sorting on t_start.
        bands = []
        scene_start, scene_end = scene["range"]
        # Simple approach: each layer becomes a shot clipped to scene range.
        for ly in layers:
            t = ly.get("t", [scene_start, scene_end])
            t_start = max(scene_start, t[0])
            t_end = min(scene_end, t[1])
            if t_end - t_start < 0.05:
                continue
            bands.append((t_start, t_end, ly))
        # Sort & resolve overlaps by keeping each later-introduced layer dominant
        bands.sort(key=lambda b: b[0])
        resolved: list[dict] = []
        cursor = scene_start
        for i, (s, e, ly) in enumerate(bands):
            if s > cursor:
                # gap → repeat previous or use solid black
                resolved.append({"t": [cursor, s], "type": "solid", "color": "bg_black"})
            shot_end = e
            # If a later band starts before our end, cut at its start.
            for s2, _e2, _ly2 in bands[i + 1:]:
                if s2 > s and s2 < shot_end:
                    shot_end = s2
                    break
            spec = {**ly, "t": [max(s, cursor), shot_end]}
            resolved.append(spec)
            cursor = shot_end
        if cursor < scene_end:
            resolved.append({"t": [cursor, scene_end], "type": "solid", "color": "bg_black"})
        return resolved
    if "composite" in scene:
        # Treat the first layer as the hero shot (it carries `src` or hero label).
        first = scene["composite"][0] if scene["composite"] else {}
        return [{
            "t": list(scene["range"]),
            "src": first.get("src"),
            "zoom": [1.00, 1.06],
            "type": first.get("type", "video"),
        }]
    # Bare scene with just description (e.g. wrapper) — fallback to a solid shot.
    return [{"t": list(scene["range"]), "type": "solid", "color": "bg_black"}]


def parse_timeline(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_scene_list(tl: dict) -> list[ParsedScene]:
    out: list[ParsedScene] = []
    for s in tl["scenes"]:
        ps = ParsedScene(id=s["id"], t_start=s["range"][0], t_end=s["range"][1], raw=s)
        ps.shots = _normalize_scene_to_shots(s)
        # Pull text overlays — they get drawn over whatever the visible shot is.
        for tx in s.get("text", []) or []:
            ps.text_overlays.append(tx)
        out.append(ps)
    return out


# ------------------------------------------------------- shot rendering -----

def _snap_shots_to_beat(scenes: list[ParsedScene], beat_sec: float,
                        scene_lock: bool = True) -> None:
    """Snap each shot's t_end to the nearest beat. Scene boundaries stay locked
    to the YAML range so VO/SFX timestamps still land where authored."""
    for sc in scenes:
        for i, shot in enumerate(sc.shots):
            t = shot["t"]
            t_start = t[0] if i == 0 else sc.shots[i - 1]["t"][1]
            if i == len(sc.shots) - 1:
                t_end = sc.t_end if scene_lock else _snap_to_beat(t[1], beat_sec)
            else:
                snapped = _snap_to_beat(t[1], beat_sec)
                # Enforce min cut interval 2.0s
                if snapped - t_start < 2.0:
                    snapped = t_start + 2.0
                t_end = min(snapped, sc.t_end)
            shot["t"] = [t_start, t_end]


def render_master_video(
    scenes: list[ParsedScene],
    *,
    project_root: Path,
    fps: int,
    canvas: tuple[int, int],
    warm_grade: bool,
    work_dir: Path,
) -> Path:
    """Render every shot, concat into a 60s silent 9:16 master video."""
    work_dir.mkdir(parents=True, exist_ok=True)
    shot_paths: list[Path] = []
    for sc in scenes:
        _log(f"  ▸ scene {sc.id}  [{sc.t_start:.2f}–{sc.t_end:.2f}]  shots={len(sc.shots)}")
        for i, spec in enumerate(sc.shots):
            t_start, t_end = spec["t"]
            out = work_dir / f"shot_{sc.id}_{i:02d}.mp4"

            # Filter overlapping text overlays to this shot window
            ovs: list[dict] = []
            for tx in sc.text_overlays:
                ts, te = tx.get("t", [t_start, t_end])
                if te <= t_start or ts >= t_end:
                    continue
                ovs.append({
                    **tx,
                    "t_rel_start": max(0.0, ts - t_start),
                    "t_rel_end": min(t_end - t_start, te - t_start),
                })

            shot = shots_mod.Shot(
                scene_id=sc.id, index=i,
                t_start=t_start, t_end=t_end,
                spec=spec, out_path=out,
            )
            shots_mod.render_shot(
                shot,
                project_root=project_root,
                fps=fps, canvas=canvas,
                warm_grade=warm_grade,
                text_overlays=ovs,
                log=_log,
            )
            shot_paths.append(out)

    silent_master = work_dir / "video_silent_master.mp4"
    shots_mod.concat_shots(shot_paths, silent_master)
    return silent_master


# ----------------------------------------------------------------- audio -----

def collect_sfx_cues(scenes: list[ParsedScene],
                     global_rules: dict) -> list[audio_mod.SfxCue]:
    cues: list[audio_mod.SfxCue] = []
    for sc in scenes:
        for c in sc.raw.get("sfx", []) or []:
            cues.append(audio_mod.SfxCue(
                t=float(c["t"]),
                file_rel=f"assets/sfx/{c['file']}",
                gain_db=float(c.get("gain_db", -12)),
                fade_in_ms=int(c.get("fade_in_ms", 0)),
                loop=bool(c.get("loop", False)),
                until=float(c["until"]) if c.get("until") is not None else None,
            ))
    # Vinyl crackle global layer
    vinyl = global_rules.get("vinyl_crackle_layer_global", {})
    if vinyl:
        scenes_present = vinyl.get("present_during", [])
        for sc in scenes:
            if sc.id in scenes_present:
                cues.append(audio_mod.SfxCue(
                    t=sc.t_start,
                    file_rel="assets/sfx/vinyl_crackle_loop.wav",
                    gain_db=float(vinyl.get("gain_db", -28)),
                    loop=True,
                    until=sc.t_end,
                ))
    return cues


def collect_vo_lines(tl: dict, lang: str) -> list[vo_mod.VoLine]:
    key = f"vo_lines_{lang}"
    out: list[vo_mod.VoLine] = []
    for ln in tl.get(key, []) or []:
        out.append(vo_mod.VoLine(
            scene=ln["scene"], t=float(ln["t"]),
            copy=ln["copy"], lang=lang,
        ))
    return out


# ------------------------------------------------------------- captions -----

def _srt_ts(t: float) -> str:
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60);   t -= m * 60
    s = int(t)
    ms = int(round((t - s) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(lines: list[vo_mod.VoLine], out_path: Path,
              *, default_dur: float = 3.5) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    for i, ln in enumerate(lines, start=1):
        start = ln.t
        end = min(60.0, start + default_dur)
        chunks.append(f"{i}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{ln.copy}\n")
    out_path.write_text("\n".join(chunks), encoding="utf-8")
    return out_path


# -------------------------------------------------------------- thumbnail ---

def make_thumbnail(master_mp4: Path, out_png: Path, t: float = 54.0) -> Path:
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{t:.3f}", "-i", str(master_mp4),
        "-frames:v", "1",
        "-vf", "scale=1080:1920:flags=lanczos",
        str(out_png),
    ]
    subprocess.run(cmd, check=True)
    return out_png


# ---------------------------------------------------------------- mux + ----

def mux_video_audio(video_mp4: Path, audio_aac: Path, out_mp4: Path) -> Path:
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video_mp4), "-i", str(audio_aac),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2",
        "-shortest",
        "-movflags", "+faststart",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True)
    return out_mp4


# ------------------------------------------------------------------ main ----

def render_pipeline(args) -> None:
    if not _ffmpeg_available():
        _log("✖ ffmpeg / ffprobe not found on PATH. Install ffmpeg first.")
        sys.exit(2)

    project_root = ROOT
    tl = parse_timeline(project_root / "timeline.yaml")
    fps = int(tl.get("fps", 30))
    duration = float(tl.get("duration_sec", 60.0))
    canvas = (int(tl["master_resolution"]["w"]), int(tl["master_resolution"]["h"]))
    bpm = float(args.bpm or tl.get("bpm", 92))
    beat_sec = round(60.0 / bpm, 4)
    warm = bool(tl.get("global_rules", {}).get("warm_color_bias", True))

    renders = project_root / "renders"
    work = project_root / "work"
    renders.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    # 0) Asset audit
    _log("\n[1/10] Asset audit ──────────────────────────────")
    audit = ph.audit_manifest(tl.get("asset_manifest", {}), project_root)
    missing = [rel for rel, present in audit if not present]
    for rel, present in audit:
        flag = "✓" if present else "·"
        _log(f"   {flag} {rel}")
    _log(f"   → {len(audit) - len(missing)} present / {len(missing)} will be synthesized")

    # 1) Build scene list, snap to beat grid
    _log("\n[2/10] Parse + beat-snap timeline ───────────────")
    scenes = build_scene_list(tl)
    _snap_shots_to_beat(scenes, beat_sec)
    _log(f"   beat_sec={beat_sec}s  bpm={bpm}  scenes={len(scenes)}")

    if args.hooks_only:
        _render_hook_variants(scenes, tl, project_root, fps, canvas, work, renders)
        return

    # 2) VO synthesis (HT primary)
    _log("\n[3/10] Generate VO lines (Haitian Creole) ───────")
    vo_dir_ht = project_root / "assets" / "vo" / "lines"
    ht_lines = collect_vo_lines(tl, "ht")
    if args.skip_vo:
        ht_paths = [vo_dir_ht / f"ht_{ln.scene}.wav" for ln in ht_lines]
        for ln, p in zip(ht_lines, ht_paths):
            ph.make_silent_wav(p, max(1.0, len(ln.copy.split()) / 2.6))
    else:
        ht_paths = vo_mod.synthesize_all(ht_lines, vo_dir_ht, log=_log)

    vo_cues_ht = [audio_mod.VoCue(t=ln.t, wav_path=p) for ln, p in zip(ht_lines, ht_paths)]
    vo_track_ht = audio_mod.build_vo_track(vo_cues_ht, work / "vo_master_ht.wav",
                                           total_duration=duration)

    # 3) Music + SFX
    _log("\n[4/10] Music bed + SFX ──────────────────────────")
    music_path = audio_mod.prepare_music_bed(
        "assets/music/bed_kompa_emotional_92bpm.wav", project_root,
        work / "music_bed.wav", total_duration=duration,
    )
    sfx_cues = collect_sfx_cues(scenes, tl.get("global_rules", {}))
    sfx_track = audio_mod.build_sfx_track(sfx_cues, work / "sfx_master.wav",
                                          project_root=project_root,
                                          total_duration=duration)

    # 4) Render shots
    _log("\n[5/10] Render shots (motion + warm grade) ───────")
    silent_video = render_master_video(
        scenes,
        project_root=project_root,
        fps=fps, canvas=canvas, warm_grade=warm,
        work_dir=work / "shots",
    )

    # 5) Mix master audio (HT)
    _log("\n[6/10] Mix master audio + loudnorm to -14 LUFS ──")
    master_audio_ht = audio_mod.mix_master(
        music_path=music_path, vo_path=vo_track_ht, sfx_path=sfx_track,
        out_path=work / "master_audio_ht.wav",
        duck_db=tl.get("global_rules", {}).get("vo_ducks_music_db", -6),
        total_duration=duration,
    )
    aac_ht = audio_mod.encode_aac(master_audio_ht, work / "master_audio_ht.m4a")

    # 6) Mux 9:16 HT master
    _log("\n[7/10] Mux 9:16 master (HT) ─────────────────────")
    master_9x16_ht = renders / "mixmaster_lakay_9x16_master_ht.mp4"
    mux_video_audio(silent_video, aac_ht, master_9x16_ht)

    # 7) EN dub variant
    _log("\n[8/10] EN dub variant (Adam) ────────────────────")
    en_lines = collect_vo_lines(tl, "en")
    vo_dir_en = project_root / "assets" / "vo" / "lines"
    if args.skip_vo:
        en_paths = [vo_dir_en / f"en_{ln.scene}.wav" for ln in en_lines]
        for ln, p in zip(en_lines, en_paths):
            ph.make_silent_wav(p, max(1.0, len(ln.copy.split()) / 2.6))
    else:
        en_paths = vo_mod.synthesize_all(en_lines, vo_dir_en, log=_log)
    vo_cues_en = [audio_mod.VoCue(t=ln.t, wav_path=p) for ln, p in zip(en_lines, en_paths)]
    vo_track_en = audio_mod.build_vo_track(vo_cues_en, work / "vo_master_en.wav",
                                           total_duration=duration)
    master_audio_en = audio_mod.mix_master(
        music_path=music_path, vo_path=vo_track_en, sfx_path=sfx_track,
        out_path=work / "master_audio_en.wav",
        duck_db=tl.get("global_rules", {}).get("vo_ducks_music_db", -6),
        total_duration=duration,
    )
    aac_en = audio_mod.encode_aac(master_audio_en, work / "master_audio_en.m4a")
    master_9x16_en = renders / "mixmaster_lakay_9x16_master_en.mp4"
    mux_video_audio(silent_video, aac_en, master_9x16_en)

    # 8) Reframes (1:1, 16:9) of HT master
    _log("\n[9/10] Reframe to 1:1 and 16:9 ──────────────────")
    reframe_mod.to_square_1080(master_9x16_ht, renders / "mixmaster_lakay_1x1_ht.mp4")
    reframe_mod.to_landscape_1920x1080(master_9x16_ht, renders / "mixmaster_lakay_16x9_ht.mp4")

    # 9) Hook variants (Scene 1 0–6s, HT VO subset)
    _render_hook_variants(scenes, tl, project_root, fps, canvas, work, renders,
                          ht_lines=ht_lines, ht_paths=ht_paths,
                          sfx_cues=sfx_cues, music_path=music_path)

    # 10) SRTs + thumbnail
    _log("\n[10/10] SRT captions + thumbnail ────────────────")
    write_srt(ht_lines, renders / "mixmaster_lakay_captions_ht.srt")
    write_srt(en_lines, renders / "mixmaster_lakay_captions_en.srt")
    make_thumbnail(master_9x16_ht, renders / "mixmaster_lakay_thumbnail.png", t=54.0)

    _log("\n✓ Pipeline complete.\n")
    _print_asset_checklist(project_root, audit)
    _print_tree(project_root)


def _render_hook_variants(scenes, tl, project_root, fps, canvas, work, renders,
                          *, ht_lines=None, ht_paths=None,
                          sfx_cues=None, music_path=None) -> None:
    _log("\n[hooks] Hook variants A/B/C ─────────────────────")
    duration = 6.0  # hook duration
    # Build a 6s VO track from any HT lines occurring inside the hook window (Scene 1 is 0–8s)
    hook_vo_cues: list[audio_mod.VoCue] = []
    if ht_lines and ht_paths:
        for ln, p in zip(ht_lines, ht_paths):
            if ln.t < duration:
                hook_vo_cues.append(audio_mod.VoCue(t=ln.t, wav_path=p))
    if music_path is None:
        music_path = audio_mod.prepare_music_bed(
            "assets/music/bed_kompa_emotional_92bpm.wav", project_root,
            work / "music_bed.wav", total_duration=60.0,
        )

    vo_track = audio_mod.build_vo_track(
        hook_vo_cues, work / "vo_hook.wav",
        total_duration=duration,
    )
    sfx_for_hook = []
    for c in (sfx_cues or []):
        if c.t < duration:
            until = c.until if c.until is None else min(c.until, duration)
            sfx_for_hook.append(audio_mod.SfxCue(
                t=c.t, file_rel=c.file_rel, gain_db=c.gain_db,
                fade_in_ms=c.fade_in_ms, loop=c.loop, until=until,
            ))
    sfx_track = audio_mod.build_sfx_track(
        sfx_for_hook, work / "sfx_hook.wav",
        project_root=project_root, total_duration=duration,
    )

    # Trim music bed to 6s
    hook_music = work / "music_hook.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(music_path), "-t", f"{duration:.3f}",
        "-c:a", "pcm_s24le", str(hook_music),
    ], check=True)

    hook_mix = audio_mod.mix_master(
        music_path=hook_music, vo_path=vo_track, sfx_path=sfx_track,
        out_path=work / "hook_audio.wav",
        duck_db=tl.get("global_rules", {}).get("vo_ducks_music_db", -6),
        total_duration=duration,
    )
    hook_aac = audio_mod.encode_aac(hook_mix, work / "hook_audio.m4a")

    for hid in ("A", "B", "C"):
        v = work / f"hook_{hid}_video.mp4"
        overlays_mod.render_hook(hid, out_path=v, project_root=project_root,
                                 fps=fps, canvas=canvas)
        out = renders / f"mixmaster_lakay_9x16_hook{hid}_ht.mp4"
        mux_video_audio(v, hook_aac, out)
        _log(f"   ✓ hook {hid} → {out.relative_to(project_root)}")


def _print_asset_checklist(project_root: Path, audit) -> None:
    _log("ASSET CHECKLIST  (drop these into /assets/ to upgrade from placeholders)")
    _log("─" * 70)
    for rel, present in audit:
        flag = "✓ HAVE        " if present else "· PLACEHOLDER "
        _log(f"  {flag} {rel}")
    _log("")
    _log("ENV VARS")
    _log("─" * 70)
    _log("  ELEVENLABS_API_KEY        required for any VO synthesis")
    _log("  ELEVENLABS_HT_VOICE_ID    required for Haitian Creole VO (cloned voice)")
    _log("                            If unset, HT lines are silent — Adam is")
    _log("                            NEVER auto-substituted for HT.")
    _log("")


def _print_tree(project_root: Path) -> None:
    _log("FILE TREE")
    _log("─" * 70)
    for p in sorted(project_root.rglob("*")):
        if "__pycache__" in p.parts or "/work/" in str(p) or p.parent.name == "work":
            continue
        if p.is_dir():
            continue
        rel = p.relative_to(project_root)
        _log(f"  {rel}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-vo", action="store_true",
                        help="Skip ElevenLabs API calls; use silence (dry pass).")
    parser.add_argument("--hooks-only", action="store_true",
                        help="Re-render hook variants only.")
    parser.add_argument("--bpm", type=float, default=None,
                        help="Override BPM (default: from YAML, 92).")
    args = parser.parse_args()
    render_pipeline(args)


if __name__ == "__main__":
    main()
