"""Per-scene visual compositors.

Replaces generic placeholder cards with hand-composed motion-graphics
backgrounds tailored to each scene. Each function produces a wide/tall PNG
"panorama" that the existing zoompan pipeline pans across to create motion.

The named lookup matches the filenames declared in timeline.yaml's asset
manifest, so when a real file is missing the renderer routes here instead
of the generic placeholder card.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

COLORS = {
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
    "haiti_red":     "#C8102E",
    "haiti_blue":    "#1E3A8A",
}


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _vertical_gradient(size, top_hex: str, bottom_hex: str) -> Image.Image:
    w, h = size
    top = _rgb(top_hex)
    bot = _rgb(bottom_hex)
    img = Image.new("RGB", (w, h), top)
    px = img.load()
    for y in range(h):
        f = y / max(1, h - 1)
        r = int(top[0] * (1 - f) + bot[0] * f)
        g = int(top[1] * (1 - f) + bot[1] * f)
        b = int(top[2] * (1 - f) + bot[2] * f)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _radial_glow(size, center, color_hex: str, radius: int, intensity: float = 1.0) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (w, h), (0, 0, 0))
    glow = Image.new("RGB", (w, h), _rgb(color_hex))
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    cx, cy = center
    for r in range(radius, 0, -8):
        a = max(0, int(220 * intensity * (1 - r / radius)))
        md.ellipse((cx - r, cy - r, cx + r, cy + r), fill=a)
    base.paste(glow, (0, 0), mask)
    return base


def _add_layer(base: Image.Image, layer: Image.Image, opacity: float = 1.0) -> Image.Image:
    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")
    if opacity < 1.0:
        alpha = layer.split()[3].point(lambda p: int(p * opacity))
        layer.putalpha(alpha)
    return Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")


def _film_grain(img: Image.Image, intensity: int = 10) -> Image.Image:
    rng = random.Random(42)
    pixels = img.load()
    w, h = img.size
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            n = rng.randint(-intensity, intensity)
            r, g, b = pixels[x, y]
            pixels[x, y] = (
                max(0, min(255, r + n)),
                max(0, min(255, g + n)),
                max(0, min(255, b + n)),
            )
    return img


def _vignette(img: Image.Image, strength: float = 0.6) -> Image.Image:
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    cx, cy = w // 2, h // 2
    max_r = int(math.hypot(w, h) / 2)
    for r in range(max_r, 0, -10):
        a = int(255 * strength * (r / max_r))
        md.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255 - a)
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(img, dark, mask)


# ─────────────────────────────────────────────────────── BNB / footage ────

def render_rain_on_window(out_png: Path, size=(1080, 1920)) -> Path:
    """Dark blue night, window streaks of rain, faint city glow outside."""
    w, h = size
    img = _vertical_gradient((w, h), "#020410", "#0B1F3A")

    # City glow outside
    glow = _radial_glow((w, h), (w // 2, int(h * 0.55)), "#3A6BD9", int(h * 0.45), 0.3)
    img = _add_layer(img, glow, 0.55)
    glow2 = _radial_glow((w, h), (int(w * 0.3), int(h * 0.65)), "#E8B84A", int(h * 0.18), 0.25)
    img = _add_layer(img, glow2, 0.4)

    draw = ImageDraw.Draw(img)
    # Rain streaks
    rng = random.Random(7)
    for _ in range(220):
        x = rng.randint(0, w)
        y = rng.randint(0, h)
        ln = rng.randint(20, 80)
        draw.line([(x, y), (x - 4, y + ln)], fill=(180, 210, 240, 90), width=2)
    # Drops
    for _ in range(80):
        x = rng.randint(0, w)
        y = rng.randint(0, h)
        r = rng.randint(2, 5)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(200, 220, 255))

    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img = _vignette(img, 0.55)
    img.save(out_png, "PNG")
    return out_png


def render_man_apartment_night(out_png: Path, size=(1080, 1920)) -> Path:
    """Silhouette of a man holding a phone, warm amber room glow."""
    w, h = size
    img = _vertical_gradient((w, h), "#0A0E18", "#1A1208")

    # Warm lamp glow
    lamp = _radial_glow((w, h), (int(w * 0.78), int(h * 0.32)), "#F2A15B", int(h * 0.30), 0.6)
    img = _add_layer(img, lamp, 0.7)

    # Silhouette — head + shoulders + arm holding phone
    draw = ImageDraw.Draw(img)
    cx = int(w * 0.42)
    head_y = int(h * 0.42)
    head_r = int(w * 0.10)
    # Body
    body_top = head_y + head_r - 10
    draw.polygon([
        (cx - int(w * 0.22), h),
        (cx + int(w * 0.26), h),
        (cx + int(w * 0.18), body_top + 50),
        (cx - int(w * 0.18), body_top + 50),
    ], fill="#000000")
    # Head
    draw.ellipse((cx - head_r, head_y - head_r, cx + head_r, head_y + head_r), fill="#000000")
    # Phone glow in hand
    phone_x, phone_y = int(w * 0.55), int(h * 0.62)
    glow_phone = _radial_glow((w, h), (phone_x, phone_y), "#00E5FF", 220, 0.55)
    img = _add_layer(img, glow_phone, 0.55)
    draw = ImageDraw.Draw(img)
    pw, ph = 58, 110
    draw.rounded_rectangle(
        (phone_x - pw // 2, phone_y - ph // 2, phone_x + pw // 2, phone_y + ph // 2),
        radius=10, fill="#0A0E18", outline="#00E5FF", width=3,
    )

    img = _vignette(img, 0.5)
    img.save(out_png, "PNG")
    return out_png


def _person_silhouette_with_phone(out_png: Path, *, label_glow: str, accent_hex: str,
                                  size=(1080, 1920)) -> Path:
    w, h = size
    img = _vertical_gradient((w, h), "#06080F", "#0E1A2C")
    glow = _radial_glow((w, h), (w // 2, int(h * 0.5)), accent_hex, int(h * 0.35), 0.4)
    img = _add_layer(img, glow, 0.5)

    draw = ImageDraw.Draw(img)
    cx = w // 2
    head_y = int(h * 0.35)
    head_r = int(w * 0.09)
    body_top = head_y + head_r - 6
    draw.polygon([
        (cx - int(w * 0.30), h),
        (cx + int(w * 0.30), h),
        (cx + int(w * 0.18), body_top + 40),
        (cx - int(w * 0.18), body_top + 40),
    ], fill="#000000")
    draw.ellipse((cx - head_r, head_y - head_r, cx + head_r, head_y + head_r), fill="#000000")
    # Phone in hand
    phone_x, phone_y = cx + int(w * 0.10), int(h * 0.58)
    pglow = _radial_glow((w, h), (phone_x, phone_y), accent_hex, 180, 0.65)
    img = _add_layer(img, pglow, 0.65)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (phone_x - 32, phone_y - 60, phone_x + 32, phone_y + 60),
        radius=10, fill="#070A12", outline=accent_hex, width=3,
    )
    img = _vignette(img, 0.5)
    img.save(out_png, "PNG")
    return out_png


def render_truck_driver_night(out_png: Path, size=(1080, 1920)) -> Path:
    return _person_silhouette_with_phone(out_png, label_glow="DRIVER",
                                         accent_hex="#E8B84A", size=size)


def render_woman_cooking(out_png: Path, size=(1080, 1920)) -> Path:
    return _person_silhouette_with_phone(out_png, label_glow="HOME COOK",
                                         accent_hex="#F2A15B", size=size)


def render_man_gym(out_png: Path, size=(1080, 1920)) -> Path:
    return _person_silhouette_with_phone(out_png, label_glow="ATHLETE",
                                         accent_hex="#14E6C0", size=size)


def render_student_late_night(out_png: Path, size=(1080, 1920)) -> Path:
    return _person_silhouette_with_phone(out_png, label_glow="STUDENT",
                                         accent_hex="#A864FF", size=size)


def render_vinyl_needle_drop(out_png: Path, size=(1080, 1920)) -> Path:
    """Vinyl record close-up, gold needle catching light."""
    w, h = size
    img = _vertical_gradient((w, h), "#0A0608", "#1F0F04")
    draw = ImageDraw.Draw(img)
    # Vinyl disc
    cx, cy = w // 2, int(h * 0.55)
    r_outer = int(w * 0.55)
    draw.ellipse((cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer), fill="#0A0808")
    for r in range(r_outer, 60, -22):
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline="#1A1010", width=2)
    # Center label
    draw.ellipse((cx - 130, cy - 130, cx + 130, cy + 130), fill="#C8102E")  # Haitian red label
    # Needle arm
    draw.polygon([(cx + 380, cy - 580), (cx + 420, cy - 600),
                  (cx + 80, cy - 80), (cx + 60, cy - 60)], fill="#E8B84A")
    # Needle glint
    g = _radial_glow((w, h), (cx + 90, cy - 90), "#FFFFFF", 70, 0.9)
    img = _add_layer(img, g, 0.8)
    img = _vignette(img, 0.55)
    img.save(out_png, "PNG")
    return out_png


def render_radio_dial(out_png: Path, size=(1080, 1920)) -> Path:
    """Radio tuning dial close-up, amber backlit."""
    w, h = size
    img = _vertical_gradient((w, h), "#08060A", "#1A1408")
    draw = ImageDraw.Draw(img)
    # Backlit amber bar
    bar_top = int(h * 0.35)
    bar_h = int(h * 0.30)
    draw.rectangle((60, bar_top, w - 60, bar_top + bar_h), fill="#3A2410")
    # Tick marks
    for i in range(20):
        x = 80 + i * ((w - 160) // 20)
        tick_h = 28 if i % 5 == 0 else 14
        draw.rectangle((x, bar_top + 30, x + 4, bar_top + 30 + tick_h), fill="#E8B84A")
    # Indicator
    ix = int(w * 0.55)
    draw.rectangle((ix - 3, bar_top + 10, ix + 3, bar_top + bar_h - 10), fill="#F2A15B")
    # Frequency text
    f = _font(72)
    draw.text((w // 2 - 200, bar_top + bar_h + 30), "92.7 FM", fill="#E8B84A", font=f)
    # Glow
    glow = _radial_glow((w, h), (w // 2, bar_top + bar_h // 2), "#F2A15B", int(h * 0.35), 0.45)
    img = _add_layer(img, glow, 0.55)
    img = _vignette(img, 0.55)
    img.save(out_png, "PNG")
    return out_png


# ───────────────────────────────────────────────── UI capture mockups ────

def _phone_frame(img: Image.Image, screen_color: str = "#0A0E18") -> tuple[int, int, int, int]:
    """Draw a phone bezel onto img and return the screen rect (x1,y1,x2,y2)."""
    w, h = img.size
    pad = 60
    body = (pad, pad + 80, w - pad, h - pad - 80)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(body, radius=70, fill="#000000", outline="#1A1F2A", width=4)
    screen = (body[0] + 22, body[1] + 22, body[2] - 22, body[3] - 22)
    draw.rounded_rectangle(screen, radius=50, fill=screen_color)
    # Notch
    nx = (body[0] + body[2]) // 2
    ny = body[1] + 22
    draw.rounded_rectangle((nx - 110, ny + 8, nx + 110, ny + 50), radius=20, fill="#000000")
    return screen


def render_app_open(out_png: Path, size=(1080, 1920)) -> Path:
    w, h = size
    img = _vertical_gradient((w, h), "#03050A", "#0A0E18")
    glow = _radial_glow((w, h), (w // 2, h // 2), "#00E5FF", int(h * 0.30), 0.35)
    img = _add_layer(img, glow, 0.45)
    screen = _phone_frame(img)
    sx1, sy1, sx2, sy2 = screen
    sw, sh = sx2 - sx1, sy2 - sy1
    draw = ImageDraw.Draw(img)
    # App icon
    icon_size = 280
    icon = (sx1 + sw // 2 - icon_size // 2, sy1 + sh // 2 - icon_size // 2 - 80,
            sx1 + sw // 2 + icon_size // 2, sy1 + sh // 2 + icon_size // 2 - 80)
    draw.rounded_rectangle(icon, radius=60, fill="#E8B84A")
    # MM monogram
    f = _font(160)
    bbox = draw.textbbox((0, 0), "MM", font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((icon[0] + (icon_size - tw) // 2, icon[1] + (icon_size - th) // 2 - 14),
              "MM", fill="#03050A", font=f)
    # App name
    f2 = _font(60)
    draw.text((sx1 + 80, sy2 - 220), "MIXMASTER PRO", fill="#FFFFFF", font=f2)
    f3 = _font(36, bold=False)
    draw.text((sx1 + 80, sy2 - 150), "TANDE LAKAY OU", fill="#E8B84A", font=f3)
    img.save(out_png, "PNG")
    return out_png


def render_live_radio_button(out_png: Path, size=(1080, 1920)) -> Path:
    w, h = size
    img = _vertical_gradient((w, h), "#03050A", "#0A0E18")
    screen = _phone_frame(img)
    sx1, sy1, sx2, sy2 = screen
    sw = sx2 - sx1
    draw = ImageDraw.Draw(img)
    # Header
    f = _font(48)
    draw.text((sx1 + 60, sy1 + 80), "MixMaster Pro", fill="#FFFFFF", font=f)
    f2 = _font(28, bold=False)
    draw.text((sx1 + 60, sy1 + 150), "Tande Lakay Ou", fill="#B7C0CF", font=f2)
    # LIVE RADIO button
    btn_y = sy1 + 280
    btn_h = 220
    btn = (sx1 + 60, btn_y, sx2 - 60, btn_y + btn_h)
    glow = _radial_glow((w, h), ((btn[0] + btn[2]) // 2, (btn[1] + btn[3]) // 2),
                        "#00E5FF", 360, 0.7)
    img = _add_layer(img, glow, 0.65)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(btn, radius=40, outline="#00E5FF", width=5, fill="#0F1A2E")
    f3 = _font(72)
    draw.text((btn[0] + 70, btn[1] + 60), "● LIVE RADIO", fill="#00E5FF", font=f3)
    f4 = _font(30, bold=False)
    draw.text((btn[0] + 70, btn[1] + 145), "Listen now from anywhere", fill="#B7C0CF", font=f4)
    # Secondary buttons
    for i, label in enumerate(["KOMPA PRESETS", "PRO MASTERING"]):
        y = btn_y + btn_h + 40 + i * 140
        b = (sx1 + 60, y, sx2 - 60, y + 110)
        draw.rounded_rectangle(b, radius=28, outline="#2A3548", width=3, fill="#0A0E18")
        draw.text((b[0] + 50, b[1] + 30), label, fill="#FFFFFF", font=_font(40))
    img.save(out_png, "PNG")
    return out_png


def render_radio_station_list(out_png: Path, size=(1080, 1920)) -> Path:
    w, h = size
    img = _vertical_gradient((w, h), "#03050A", "#0A0E18")
    screen = _phone_frame(img)
    sx1, sy1, sx2, sy2 = screen
    draw = ImageDraw.Draw(img)
    f = _font(44)
    draw.text((sx1 + 60, sy1 + 80), "LIVE FROM AYITI", fill="#E8B84A", font=f)
    stations = [
        ("Radio Métropole", "104.5 FM • Port-au-Prince"),
        ("Radio Caraïbes",  "94.5 FM • Port-au-Prince"),
        ("Radio Kiskeya",   "88.5 FM • Pétion-Ville"),
        ("Radio One",       "92.7 FM • Port-au-Prince"),
        ("Signal FM",       "90.5 FM • Port-au-Prince"),
        ("Radio Vision 2000","99.3 FM • Port-au-Prince"),
        ("Magik 9",         "100.9 FM • Pétion-Ville"),
    ]
    fy = _font(38)
    fs = _font(24, bold=False)
    y = sy1 + 180
    for name, sub in stations:
        # Station card
        card = (sx1 + 60, y, sx2 - 60, y + 130)
        draw.rounded_rectangle(card, radius=20, fill="#0F1A2E", outline="#1A2540", width=2)
        # Status dot
        draw.ellipse((card[0] + 30, card[1] + 50, card[0] + 60, card[1] + 80), fill="#FF4FA3")
        draw.text((card[0] + 90, card[1] + 30), name, fill="#FFFFFF", font=fy)
        draw.text((card[0] + 90, card[1] + 80), sub, fill="#B7C0CF", font=fs)
        y += 150
        if y > sy2 - 100:
            break
    img.save(out_png, "PNG")
    return out_png


def render_equalizer(out_png: Path, size=(1080, 1920)) -> Path:
    w, h = size
    img = _vertical_gradient((w, h), "#03050A", "#0A0E18")
    glow = _radial_glow((w, h), (w // 2, int(h * 0.55)), "#14E6C0", int(h * 0.32), 0.5)
    img = _add_layer(img, glow, 0.55)
    screen = _phone_frame(img)
    sx1, sy1, sx2, sy2 = screen
    draw = ImageDraw.Draw(img)
    f = _font(40)
    draw.text((sx1 + 60, sy1 + 80), "NOW PLAYING — Radio Métropole", fill="#FFFFFF", font=f)
    # Equalizer bars
    bars = 24
    bar_w = ((sx2 - sx1 - 120) // bars) - 6
    base_y = sy1 + 800
    rng = random.Random(11)
    for i in range(bars):
        bx = sx1 + 60 + i * (bar_w + 6)
        bh = rng.randint(60, 480)
        # Color gradient by height
        if bh > 360:
            col = "#FF4FA3"
        elif bh > 240:
            col = "#14E6C0"
        else:
            col = "#00E5FF"
        draw.rounded_rectangle((bx, base_y - bh, bx + bar_w, base_y), radius=6, fill=col)
    # Track scrubber
    sy = base_y + 100
    draw.rounded_rectangle((sx1 + 60, sy, sx2 - 60, sy + 8), radius=4, fill="#1A2540")
    pw = int((sx2 - sx1 - 120) * 0.35)
    draw.rounded_rectangle((sx1 + 60, sy, sx1 + 60 + pw, sy + 8), radius=4, fill="#14E6C0")
    img.save(out_png, "PNG")
    return out_png


def render_kompa_presets(out_png: Path, size=(1080, 1920)) -> Path:
    w, h = size
    img = _vertical_gradient((w, h), "#03050A", "#0A0E18")
    screen = _phone_frame(img)
    sx1, sy1, sx2, sy2 = screen
    draw = ImageDraw.Draw(img)
    draw.text((sx1 + 60, sy1 + 80), "KOMPA PRESETS", fill="#E8B84A", font=_font(44))
    presets = [
        ("KOMPA WARM",       "Vintage tape warmth + soft compression"),
        ("KOMPA STEREO",     "Wide stereo image, lifted highs"),
        ("CARIBBEAN SHINE",  "Bright top end, rounded low end"),
        ("KOMPA WARM PRO",   "Signature: warm + stereo + presence"),
    ]
    y = sy1 + 200
    for i, (name, sub) in enumerate(presets):
        card = (sx1 + 60, y, sx2 - 60, y + 200)
        # Highlight last preset (signature)
        if i == 3:
            glow = _radial_glow((w, h), ((card[0] + card[2]) // 2, (card[1] + card[3]) // 2),
                                "#E8B84A", 380, 0.7)
            img = _add_layer(img, glow, 0.55)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(card, radius=24, fill="#1A1408", outline="#E8B84A", width=4)
            # Badge
            draw.rounded_rectangle((card[2] - 220, card[1] + 20, card[2] - 30, card[1] + 70),
                                   radius=12, fill="#E8B84A")
            draw.text((card[2] - 200, card[1] + 24), "SIGNATURE", fill="#03050A", font=_font(28))
        else:
            draw.rounded_rectangle(card, radius=24, fill="#0F1A2E", outline="#1A2540", width=2)
        draw.text((card[0] + 40, card[1] + 50), name, fill="#FFFFFF", font=_font(48))
        draw.text((card[0] + 40, card[1] + 120), sub, fill="#B7C0CF", font=_font(28, bold=False))
        y += 220
    img.save(out_png, "PNG")
    return out_png


def render_before_after(out_png: Path, size=(1080, 1920)) -> Path:
    w, h = size
    img = _vertical_gradient((w, h), "#03050A", "#0A0E18")
    screen = _phone_frame(img)
    sx1, sy1, sx2, sy2 = screen
    draw = ImageDraw.Draw(img)
    draw.text((sx1 + 60, sy1 + 80), "MASTER PREVIEW", fill="#FFFFFF", font=_font(44))
    # Two waveforms
    rng = random.Random(3)
    sw, sh = sx2 - sx1, sy2 - sy1
    # AVAN (before) — narrower dynamic range
    box1 = (sx1 + 60, sy1 + 220, sx2 - 60, sy1 + 220 + 360)
    draw.rounded_rectangle(box1, radius=24, fill="#0F1A2E", outline="#2A3548", width=2)
    draw.text((box1[0] + 30, box1[1] + 20), "AVAN", fill="#B7C0CF", font=_font(32))
    cx, cy = (box1[0] + box1[2]) // 2, (box1[1] + box1[3]) // 2 + 30
    for x in range(box1[0] + 50, box1[2] - 50, 6):
        amp = rng.randint(15, 70)
        draw.line([(x, cy - amp), (x, cy + amp)], fill="#7B8A9F", width=3)
    # APRE (after) — full dynamic, wide
    box2 = (sx1 + 60, sy1 + 620, sx2 - 60, sy1 + 620 + 360)
    glow = _radial_glow((w, h), ((box2[0] + box2[2]) // 2, (box2[1] + box2[3]) // 2),
                        "#14E6C0", 380, 0.5)
    img = _add_layer(img, glow, 0.5)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(box2, radius=24, fill="#082018", outline="#14E6C0", width=3)
    draw.text((box2[0] + 30, box2[1] + 20), "APRE", fill="#14E6C0", font=_font(32))
    cy = (box2[1] + box2[3]) // 2 + 30
    for x in range(box2[0] + 50, box2[2] - 50, 6):
        amp = rng.randint(60, 150)
        draw.line([(x, cy - amp), (x, cy + amp)], fill="#14E6C0", width=3)
    img.save(out_png, "PNG")
    return out_png


def render_phone_floating_neon(out_png: Path, size=(1080, 1920)) -> Path:
    """Hero shot — phone floating in neon particles."""
    w, h = size
    img = _vertical_gradient((w, h), "#040308", "#0E0820")
    # Big radial glow behind phone
    glow1 = _radial_glow((w, h), (w // 2, h // 2), "#FF4FA3", int(h * 0.35), 0.55)
    img = _add_layer(img, glow1, 0.55)
    glow2 = _radial_glow((w, h), (w // 2, h // 2), "#E8B84A", int(h * 0.22), 0.6)
    img = _add_layer(img, glow2, 0.55)

    # Particles
    rng = random.Random(13)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for _ in range(180):
        x = rng.randint(0, w)
        y = rng.randint(0, h)
        r = rng.randint(2, 8)
        col = rng.choice([(0, 229, 255, 200), (255, 79, 163, 180), (232, 184, 74, 220)])
        od.ellipse((x - r, y - r, x + r, y + r), fill=col)
    img = _add_layer(img, overlay, 1.0)

    # Phone body
    draw = ImageDraw.Draw(img)
    pw, ph = int(w * 0.45), int(h * 0.55)
    px = (w - pw) // 2
    py = (h - ph) // 2
    draw.rounded_rectangle((px - 6, py - 6, px + pw + 6, py + ph + 6),
                           radius=70, fill="#000000", outline="#E8B84A", width=4)
    draw.rounded_rectangle((px + 14, py + 14, px + pw - 14, py + ph - 14),
                           radius=58, fill="#0A0E18")
    # Logo on phone
    f = _font(96)
    draw.text((px + pw // 2 - 110, py + ph // 2 - 80), "MM", fill="#E8B84A", font=f)
    f2 = _font(36)
    draw.text((px + pw // 2 - 200, py + ph // 2 + 30), "MIXMASTER PRO", fill="#FFFFFF", font=f2)

    img.save(out_png, "PNG")
    return out_png


def render_neon_particles(out_png: Path, size=(1080, 1920)) -> Path:
    """Just particles, no phone — used as additive layer."""
    w, h = size
    img = Image.new("RGB", (w, h), (0, 0, 0))
    rng = random.Random(21)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for _ in range(250):
        x = rng.randint(0, w)
        y = rng.randint(0, h)
        r = rng.randint(3, 10)
        col = rng.choice([(0, 229, 255, 220), (255, 79, 163, 200), (232, 184, 74, 240)])
        od.ellipse((x - r, y - r, x + r, y + r), fill=col)
    img = _add_layer(img, overlay, 1.0)
    img.save(out_png, "PNG")
    return out_png


# ─────────────────────────────────────────────────────── lookup table ────

RENDERERS = {
    "bnb/rain_on_window":          render_rain_on_window,
    "bnb/man_apartment_night":     render_man_apartment_night,
    "bnb/truck_driver_night":      render_truck_driver_night,
    "bnb/woman_cooking_haitian_food": render_woman_cooking,
    "bnb/man_gym_headphones":      render_man_gym,
    "bnb/student_late_night":      render_student_late_night,
    "bnb/vinyl_needle_drop":       render_vinyl_needle_drop,
    "bnb/radio_dial_closeup":      render_radio_dial,
    "ui_captures/01_app_open":            render_app_open,
    "ui_captures/02_live_radio_button":   render_live_radio_button,
    "ui_captures/03_radio_station_list":  render_radio_station_list,
    "ui_captures/04_equalizer_reactive":  render_equalizer,
    "ui_captures/05_kompa_presets":       render_kompa_presets,
    "ui_captures/06_before_after_master": render_before_after,
    "hero/phone_floating_neon":           render_phone_floating_neon,
    "hero/neon_particles":                render_neon_particles,
}


def find_renderer(relpath: str):
    """Return a renderer fn for a known asset path, or None."""
    key = relpath.replace("\\", "/")
    if key.startswith("assets/"):
        key = key[len("assets/"):]
    # strip extension
    if "." in key.rsplit("/", 1)[-1]:
        key = key.rsplit(".", 1)[0]
    return RENDERERS.get(key)
