"""Sharepic-Generator FT Adler Kiel Handballdamen (1080x1350, Instagram 4:5).

Design angelehnt an den Post "Trainingsspiel – Partner gesucht":
Mint-Verlauf, schwarze kursive Schmalschrift, Falken-Logo, Teamfoto, Schild, schwarze Fußleiste.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE = Path(__file__).parent
W, H = 1080, 1350
MINT = (170, 238, 200)
WHITE = (246, 248, 247)
BLACK = (14, 14, 14)
INK = BLACK  # Schriftfarbe, wird je Design gesetzt
THEMES = ("dunkel", "linien", "hell")
LAYOUT = {"cx": 540, "c1": 270, "c2": 810, "card": 400, "card_r": 360, "namew": 470, "headw": 940, "vs": 110}
EDGE_LAYOUT = {"cx": 615, "c1": 395, "c2": 835, "card": 340, "card_r": 320, "namew": 410, "headw": 850, "vs": 96}
NORMAL_LAYOUT = dict(LAYOUT)
YF = {"dy": 0, "s": 1.0}


def Y(y):
    """Vertikale Position je Format (4:5 unverändert, 9:16 gestreckt und in die Story-Sicherheitszone gesetzt)."""
    return int(YF["dy"] + 90 + (y - 90) * YF["s"])


def set_format(fmt):
    global H
    if fmt == "9:16":
        H = 1920
        YF.update(dy=240, s=1.12)
    else:
        H = 1350
        YF.update(dy=0, s=1.0)
EDGE_W = 150  # Breite der Winkel-Spalte am linken Bildrand

FOOTER_ADDR = "FT Adler Kiel | Petersweg 1 | 24116 Kiel | www.ft-adler-kiel.com/sport/handball"
HANDLE = "@ADLERHANDBALLMAEDELS"
FB = "FT ADLER KIEL HANDBALLDAMEN"

WEEKDAYS = ["MONTAG", "DIENSTAG", "MITTWOCH", "DONNERSTAG", "FREITAG", "SAMSTAG", "SONNTAG"]


def font(style, size):
    return ImageFont.truetype(str(BASE / f"BarlowCondensed-{style}.ttf"), size)


def court_lines(alpha=20):
    """Handball-Spielfeld abstrakt: 6-m-Linie (durchgezogen) und 9-m-Linie (gestrichelt) an beiden Toren."""
    S = 2
    ov = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    col = (255, 255, 255, alpha)
    for cx, cy, a0 in [(W // 2, -110, 0), (W // 2, H + 110, 180)]:
        r6, r9 = 600, 900
        box = lambda r: [(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S]
        od.arc(box(r6), a0, a0 + 180, fill=col, width=7 * S)
        ang = a0
        while ang < a0 + 180:
            od.arc(box(r9), ang, ang + 3.2, fill=col, width=7 * S)
            ang += 6.4
    return ov.resize((W, H), Image.LANCZOS)


def eagle_mask(height=1400):
    """Adler (Wappen-Falke mit Handball) als scharfe, glatte Silhouette, groß skaliert."""
    g = Image.open(BASE / "falcon_raw.png").convert("L")
    k = 10
    g = g.resize((g.width * k, g.height * k), Image.LANCZOS).filter(ImageFilter.GaussianBlur(k * 0.35))
    a = g.point(lambda v: 255 if v < 118 else (0 if v > 142 else int(255 * (142 - v) / 24)))
    w = int(a.width * height / a.height)
    return a.resize((w, height), Image.LANCZOS)


def add_eagle(base, alpha=62):
    tall = H > 1500
    m = eagle_mask(1750 if tall else 1400)
    layer = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    mm = m.point(lambda v: int(v * alpha / 255))
    layer.paste((255, 255, 255, 255), (-40, 342) if tall else (-10, 190), mm)
    base.alpha_composite(layer)


def background(theme="dunkel"):
    import random
    if theme == "hell":
        return Image.new("RGB", (W, H), (255, 255, 255))
    # dunkel: weicher Lichtkegel, Hallenlinien, feines Korn
    base = Image.new("RGB", (W, H), (12, 12, 13))
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse([-250, 150, W + 250, H - 100], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    base = Image.composite(Image.new("RGB", (W, H), (34, 34, 36)), base, glow)
    base = base.convert("RGBA")
    if theme == "linien":
        base.alpha_composite(court_lines(22))
    else:
        add_eagle(base)
    rnd = random.Random(7)
    grain = Image.effect_noise((W, H), 14).convert("L").point(lambda v: int(v * 0.10))
    base = Image.composite(Image.new("RGBA", (W, H), (255, 255, 255, 255)), base, grain)
    return base.convert("RGB")


def set_theme(theme):
    global INK
    INK = BLACK if theme == "hell" else (255, 255, 255)
    LAYOUT.clear()
    LAYOUT.update(NORMAL_LAYOUT)


def fit_text(draw, text, style, max_w, start, min_size=20):
    size = start
    while size > min_size:
        f = font(style, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return font(style, min_size)


def centered(draw, y, text, f, fill=None, cx=None):
    fill = fill or INK
    cx = LAYOUT["cx"] if cx is None else cx
    w = draw.textlength(text, font=f)
    draw.text((cx - w / 2, y), text, font=f, fill=fill)


def footer(img, d):
    d.rectangle([0, 1248, W, 1292], fill=BLACK)
    f = font("Medium", 26)
    d.text((44, 1252), FOOTER_ADDR, font=f, fill=(255, 255, 255))
    f2 = font("SemiBoldItalic", 30)
    d.text((44, 1300), HANDLE, font=f2, fill=BLACK)
    d.text((560, 1300), FB, font=f2, fill=BLACK)


def shield(img, x, y, h=210):
    s = Image.open(BASE / "shield.png")
    s = s.resize((int(s.width * h / s.height), h), Image.LANCZOS)
    img.paste(s, (x, y), s)


def falcon(img, x, y, h=130):
    s = Image.open(BASE / "falcon.png")
    s = s.resize((int(s.width * h / s.height), h), Image.LANCZOS)
    img.paste(s, (x, y), s)


def icon_calendar(d, x, y, s=44):
    d.rounded_rectangle([x, y + 8, x + s, y + s], 5, outline=INK, width=4)
    d.line([x, y + 22, x + s, y + 22], fill=INK, width=4)
    d.line([x + 12, y, x + 12, y + 14], fill=INK, width=4)
    d.line([x + s - 12, y, x + s - 12, y + 14], fill=INK, width=4)


def icon_pin(d, x, y, s=44):
    d.ellipse([x + 6, y, x + s - 6, y + s - 12], outline=INK, width=4)
    d.polygon([(x + 10, y + s - 20), (x + s // 2, y + s + 4), (x + s - 10, y + s - 20)], fill=INK)
    d.ellipse([x + s // 2 - 6, y + 12, x + s // 2 + 6, y + 24], fill=INK)




def icon_clock(d, x, y, s=44):
    d.ellipse([x, y, x + s, y + s], outline=INK, width=4)
    d.line([x + s // 2, y + 9, x + s // 2, y + s // 2], fill=INK, width=4)
    d.line([x + s // 2, y + s // 2, x + s - 11, y + s // 2 + 6], fill=INK, width=4)


import io, hashlib
from PIL import ImageChops

CREST_DIR = BASE / "crests"
LOCAL_FALLBACKS = {"ft adler kiel": BASE / "shield.png",
                   "eckernförder mtv": BASE / "eckernfoerde.png"}


def clean_background(im, name=""):
    """Wappen ohne echte Transparenz mit einfarbig schwarzem Rand: Rand ab den Ecken transparent machen."""
    im = im.convert("RGBA")
    amin = im.getchannel("A").getextrema()[0]
    corner = im.getpixel((0, 0))
    print(f"  Wappen {name}: {im.size}, Alpha min {amin}, Ecke {corner}", flush=True)
    if amin < 250 or max(corner[:3]) > 40:         # echte Transparenz oder kein schwarzer Rand
        return im
    rgb, sentinel = im.convert("RGB"), (255, 0, 255)
    for pt in ((0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)):
        if rgb.getpixel(pt)[0] < 60:
            ImageDraw.floodfill(rgb, pt, sentinel, thresh=45)
    mask = Image.eval(ImageChops.difference(rgb, Image.new("RGB", im.size, sentinel)).convert("L"), lambda v: 255 if v else 0)
    im.putalpha(ImageChops.multiply(im.getchannel("A"), mask))
    return im


def load_crest(name, url=None):
    """Vereinswappen holen: Cache -> Download (Handball.net-Bildserver) -> lokale Datei -> Monogramm."""
    CREST_DIR.mkdir(parents=True, exist_ok=True)
    local = LOCAL_FALLBACKS.get(name.lower())          # saubere Repo-Wappen haben Vorrang vor dem Server-Bild
    if local and local.exists():
        return Image.open(local).convert("RGBA")
    cache = CREST_DIR / (hashlib.md5((url or name).encode()).hexdigest() + ".img")
    if cache.exists():
        return clean_background(Image.open(cache), name)
    if url:
        try:
            import requests
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            cache.write_bytes(r.content)
            return clean_background(Image.open(io.BytesIO(r.content)), name)
        except Exception:
            pass
    fb = LOCAL_FALLBACKS.get(name.lower())
    if fb and fb.exists():
        return Image.open(fb).convert("RGBA")
    # Monogramm als Notlösung
    im = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    dd = ImageDraw.Draw(im)
    dd.ellipse([20, 20, 380, 380], fill=BLACK)
    ini = "".join(w[0] for w in name.split()[:2]).upper()
    f = font("ExtraBoldItalic", 190)
    w = dd.textlength(ini, font=f)
    dd.text((200 - w / 2, 95), ini, font=f, fill=(255, 255, 255))
    return im


def crest_card(img, cx, y, size, crest):
    """Wappen auf weißer, leicht schattierter Karte."""
    x = cx - size // 2
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([x, y + 10, x + size, y + size + 10], 36, fill=(0, 0, 0, 90 if INK == BLACK else 150))
    sh = sh.filter(ImageFilter.GaussianBlur(14))
    img.paste(sh, (0, 0), sh)
    card = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle([0, 0, size - 1, size - 1], 36, fill=(255, 255, 255, 255))
    pad = 34
    c = crest.copy()
    c.thumbnail((size - 2 * pad, size - 2 * pad), Image.LANCZOS)
    card.paste(c, ((size - c.width) // 2, (size - c.height) // 2), c)
    img.paste(card, (x, y), card)


def team_name(d, cx, y, name, max_w=470):
    t = name.upper()
    centered(d, y, t, fit_text(d, t, "BoldItalic", max_w, 66), cx=cx)


def info_block(d, y0, rows, f, gap=80):
    """rows: [(icon, text)] – Icons stehen linksbündig in einer Spalte, der Block ist zentriert."""
    wmax = max(d.textlength(t, font=f) for _, t in rows)
    x0 = LAYOUT["cx"] - (wmax + 70) / 2
    for i, (icon, text) in enumerate(rows):
        y = y0 + i * gap
        icon(d, int(x0), y + 4)
        d.text((x0 + 70, y), text, font=f, fill=INK)


def announcement(game, out, theme="dunkel", fmt="4:5"):
    """game: dict(home, away, date, hall, home_crest_url?, away_crest_url?)"""
    set_format(fmt)
    set_theme(theme)
    L = LAYOUT
    img = background(theme)
    d = ImageDraw.Draw(img)
    t = "NÄCHSTES SPIEL:"
    centered(d, Y(90), t, fit_text(d, t, "ExtraBoldItalic", L["headw"], 170))

    size, cy = L["card"], Y(350)
    crest_card(img, L["c1"], cy, size, load_crest(game["home"], game.get("home_crest_url")))
    crest_card(img, L["c2"], cy, size, load_crest(game["away"], game.get("away_crest_url")))
    d = ImageDraw.Draw(img)
    centered(d, cy + size // 2 - 56, "VS", font("ExtraBoldItalic", L["vs"]))
    team_name(d, L["c1"], cy + size + 40, game["home"], L["namew"])
    team_name(d, L["c2"], cy + size + 40, game["away"], L["namew"])

    dt = game["date"]
    f = font("BoldItalic", 64)
    info_block(d, Y(975), [
        (icon_calendar, f"{WEEKDAYS[dt.weekday()]}, {dt.strftime('%d.%m.%Y')}"),
        (icon_clock, f"ANPFIFF {dt.strftime('%H:%M')} UHR"),
        (icon_pin, game["hall"].upper()),
    ], f, gap=int(92 * YF["s"]))
    img.save(out, quality=95)
    return out


def result(game, out, theme="dunkel", fmt="4:5"):
    """game: dict(home, away, goals_home, goals_away, halftime?, us_home, *_crest_url?)"""
    set_format(fmt)
    set_theme(theme)
    L = LAYOUT
    img = background(theme)
    d = ImageDraw.Draw(img)
    gh, ga = game["goals_home"], game["goals_away"]
    us, them = (gh, ga) if game["us_home"] else (ga, gh)
    head = "SIEG!" if us > them else ("UNENTSCHIEDEN" if us == them else "ABPFIFF")
    centered(d, Y(90), head, fit_text(d, head, "ExtraBoldItalic", L["headw"], 170))

    size, cy = L["card_r"], Y(330)
    crest_card(img, L["c1"], cy, size, load_crest(game["home"], game.get("home_crest_url")))
    crest_card(img, L["c2"], cy, size, load_crest(game["away"], game.get("away_crest_url")))
    d = ImageDraw.Draw(img)
    team_name(d, L["c1"], cy + size + 36, game["home"], L["namew"])
    team_name(d, L["c2"], cy + size + 36, game["away"], L["namew"])
    score = f"{gh} : {ga}"
    centered(d, Y(830), score, fit_text(d, score, "ExtraBoldItalic", L["headw"] - 40, 300))
    if game.get("halftime"):
        centered(d, Y(1200), f"HALBZEIT  {game['halftime']}", font("MediumItalic", 48))
    img.save(out, quality=95)
    return out
