# mixmaster_lakay_v1

A Python + FFmpeg pipeline that renders the **MixMaster Pro — Tande Lakay Ou
(Lakay Launch — Haitian Creole / Kompa Edition)** 60-second cinematic promo from
a single YAML spec.

The pipeline is asset-tolerant: missing footage, music, SFX, logos, and VO are
auto-replaced with clearly-labeled placeholders so the build never fails. Drop
real assets into `assets/` and re-run to upgrade.

## What it produces

```
renders/
  mixmaster_lakay_9x16_master_ht.mp4    # 1080x1920 30fps, Haitian Creole VO
  mixmaster_lakay_9x16_master_en.mp4    # 1080x1920 30fps, Adam (English) dub
  mixmaster_lakay_1x1_ht.mp4            # 1080x1080 center-crop reframe
  mixmaster_lakay_16x9_ht.mp4           # 1920x1080 with blurred pillarbox
  mixmaster_lakay_9x16_hookA_ht.mp4     # rain on window + radio static
  mixmaster_lakay_9x16_hookB_ht.mp4     # tuning dial sweep
  mixmaster_lakay_9x16_hookC_ht.mp4     # vinyl needle drop reveal
  mixmaster_lakay_captions_ht.srt
  mixmaster_lakay_captions_en.srt
  mixmaster_lakay_thumbnail.png         # 1080x1920 hero frame from t=54s
```

All MP4s are h264 / yuv420p video + AAC 320k 48kHz stereo audio, mastered to
**−14 LUFS integrated / −1 dBTP** with VO sidechain-ducking the Kompa bed by
−6 dB (200 ms attack / 400 ms release).

## Install

```bash
# 1) ffmpeg + ffprobe on PATH (required)
#    macOS:   brew install ffmpeg
#    Ubuntu:  sudo apt-get install -y ffmpeg

# 2) Python deps
pip install -r requirements.txt
```

Tested with ffmpeg 6.x and Python 3.10+.

## Run

```bash
# placeholder dry pass — no assets, no API key required, builds everything
python render.py --skip-vo

# full render with ElevenLabs VO
export ELEVENLABS_API_KEY=sk_...
export ELEVENLABS_HT_VOICE_ID=<your_cloned_haitian_male_voice_id>
python render.py

# regenerate just the three hook variants
python render.py --hooks-only

# retarget BPM (rebuilds the beat grid that hard cuts snap to)
python render.py --bpm 100
```

## Asset checklist

Drop real files into these paths to upgrade from placeholders. Missing files
become labeled placeholder cards (video) or silence (audio) automatically.

```
assets/bnb/
  rain_on_window.mp4
  man_apartment_night.mp4         # Haitian man, modern NYC/Miami apt
  truck_driver_night.mp4
  woman_cooking_haitian_food.mp4  # griot, diri ak pwa, sòs pwa
  man_gym_headphones.mp4
  student_late_night.mp4
  vinyl_needle_drop.mp4
  radio_dial_closeup.mp4

assets/ui_captures/
  01_app_open.mp4
  02_live_radio_button.mp4
  03_radio_station_list.mp4
  04_equalizer_reactive.mp4
  05_kompa_presets.mp4
  06_before_after_master.mp4

assets/hero/
  phone_floating_neon.mov         # alpha
  neon_particles.mov              # alpha

assets/music/
  bed_kompa_emotional_92bpm.wav   # 60s, builds: pad → +bass → +full drums

assets/sfx/
  radio_static.wav      radio_tune_sweep.wav   vinyl_crackle_loop.wav
  rain_window_loop.wav  crowd_ambience_warm.wav ui_tap_soft.wav
  riser_emotional.wav   preset_impact_warm.wav  shimmer_gold.wav
  confirm_chime.wav

assets/logos/
  mixmaster_logo.svg    appstore_badge.svg

assets/vo/
  vo_haitian_male_master.wav      # OR generate via ElevenLabs (see VO setup)
  vo_adam_english_master.wav      # generated automatically when API key set
```

## VO setup — important

ElevenLabs is called **per line** so timing matches the YAML exactly. Generated
WAVs are written to `assets/vo/lines/<lang>_<scene>.wav` and assembled into a
60-second master VO track by silence-padding to the timestamps in the YAML.

### Required env vars

| Var | Purpose | Required for |
| --- | --- | --- |
| `ELEVENLABS_API_KEY` | ElevenLabs account API key | Any VO synthesis |
| `ELEVENLABS_HT_VOICE_ID` | Your **cloned** Haitian male voice ID | Haitian Creole VO |

Adam (`pNInz6obpgDQGcFmaJgB`) is hard-coded for the English dub variant only.

### Why Adam is not used for the primary Haitian Creole cut

ElevenLabs default voices do not produce native-sounding Haitian Creole. The
Lakay Launch lives or dies on cultural authenticity, so Adam — a confident
American English voice — would actively undermine it. The pipeline therefore
**refuses to substitute Adam for HT lines**, even as a fallback. If
`ELEVENLABS_HT_VOICE_ID` is unset, HT lines render as silence and a clear
warning is printed; supply either:

1. A custom voice cloned from a Haitian male speaker in your account, **or**
2. An externally-recorded VO at `assets/vo/vo_haitian_male_master.wav`
   (drop in the file and the pipeline will use it directly).

### Swap to an externally-recorded Haitian VO file

```bash
# Replace the master VO file
cp /path/to/your/recorded_vo.wav assets/vo/vo_haitian_male_master.wav

# Also drop per-line WAVs (named by scene id) so the pipeline can place each
# line precisely on the YAML timestamps:
cp scene1.wav assets/vo/lines/ht_1_opening.wav
cp scene2.wav assets/vo/lines/ht_2_app_reveal.wav
# ...etc

python render.py --skip-vo   # --skip-vo skips the API but still uses your wavs
```

## Beat grid + cut snapping

All hard cuts are snapped to the 92 BPM (Kompa) beat grid where
`beat_sec = 0.652s`. Override with `--bpm`:

```bash
python render.py --bpm 100
```

Scene boundaries stay locked to the YAML `range:` so VO and SFX timestamps
remain frame-accurate.

## Mix architecture

```
music_bed (Kompa, 60s) ──▶ sidechain_compress(thr=0.05, ratio=6,
                                              attack=200ms, release=400ms)
                              ▲
                              │ key
                vo_track ─────┤
                              ▼
              ducked_music + vo + sfx ─▶ loudnorm(I=-14, TP=-1, LRA=11) ─▶ AAC 320k
```

VO is synthesized per line and post-processed (highpass 80 Hz, light de-esser
where available, 2.5:1 compression at −18 dB threshold, light plate tail).

## Visual rules enforced by the renderer

* No static frames — every shot has a zoom/pan covering ≥ 1.02× over its duration.
* Warm color bias (amber tilt) for the nostalgic grade when
  `global_rules.warm_color_bias: true`.
* Lower-third text is positioned to clear the TikTok 9:16 safe zone (top 220 px,
  bottom 480 px reserved for platform UI).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ffmpeg: command not found` | Install ffmpeg and ffprobe; ensure both are on `PATH`. |
| `⚠ ELEVENLABS_HT_VOICE_ID is not set` | Set the env var or drop a recorded HT VO at `assets/vo/vo_haitian_male_master.wav`. |
| Filter chain error mentioning `deesser` | Older ffmpeg builds lack `deesser`; the pipeline retries without it. Update ffmpeg to silence the retry. |
| Renders look like labeled cards with diagonal stripes | You're seeing **placeholders** — drop real footage into `assets/` and re-run. |

## Layout

```
mixmaster_lakay_v1/
├── render.py              # orchestrator
├── timeline.yaml          # full spec (scenes, VO, SFX, deliverables)
├── requirements.txt
├── README.md
├── lib/
│   ├── placeholders.py    # synthesizes any missing asset
│   ├── vo_elevenlabs.py   # per-line VO, language-aware routing
│   ├── shots.py           # per-shot motion, color, drawtext overlays
│   ├── overlays.py        # hook A/B/C visual variants
│   ├── audio.py           # music bed + VO + SFX + sidechain mix + loudnorm
│   └── reframe.py         # 1:1 and 16:9 reframes
├── assets/                # drop real assets here (see checklist)
└── renders/               # all deliverables land here
```
