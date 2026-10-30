#!/usr/bin/env python3
"""FT Adler Kiel Handballdamen – Posting-Autopilot.

Ablauf pro Lauf (läuft stündlich in GitHub Actions, kein eigener Server nötig):
  1. Teamseite(n) auf handball.net laden, Wappen-URLs direkt aus dem HTML ziehen.
  2. Spiele per Claude aus dem Seitentext extrahieren (robust gegen Layout-Änderungen).
  3. Ankündigung: ab 3 Tage vor Anpfiff (ab 12 Uhr) Sharepic + Caption -> Buffer (Instagram).
  4. Ergebnis: sobald der Endstand online ist Sharepic + Spielbericht (von Claude) -> Buffer.
  5. state.json merkt sich, was schon gepostet wurde (kein Doppelposting).

Aufruf:
  python bot.py                      # echter Lauf
  python bot.py --dry-run            # nichts posten, nur rendern + Payload in out/ ablegen
  python bot.py --dry-run --fixture tests/games.json --now 2026-10-29T13:00
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import sharepics as sp

BASE = Path(__file__).parent
TZ = ZoneInfo("Europe/Berlin")
STATE_FILE = BASE / "state.json"
POSTS_DIR = BASE / "posts"
OUT_DIR = BASE / "out"

TEAM_MATCH = os.getenv("TEAM_MATCH", "ADLER")                      # Teil des Teamnamens auf handball.net
DESIGN = os.getenv("DESIGN", "dunkel")                             # dunkel | hell | linien
SOURCES = [u.strip() for u in os.getenv(
    "HANDBALL_URLS",
    "https://www.handball.net/team/81928?season_id=2627,"
    "https://www.handball.net/ligen/7949?phaseId=12978&season_id=2627",
).split(",") if u.strip()]
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
CHANNEL_HINT = os.getenv("BUFFER_CHANNEL", "adlerhandballmaedels").lower()
HASHTAGS = os.getenv("HASHTAGS", "#handball #ftadlerkiel #kiel #handballdamen")
ANNOUNCE_DAYS = int(os.getenv("ANNOUNCE_DAYS", 3))                 # Ankündigung so viele Tage vor dem Spiel ...
ANNOUNCE_HOUR = int(os.getenv("ANNOUNCE_HOUR", 12))                # ... um diese Uhrzeit (Europe/Berlin)
UA = {"User-Agent": "Mozilla/5.0 (compatible; AdlerAutopilot/1.0)"}


# --------------------------------------------------------------------------- Hilfen
def log(*a):
    print(*a, flush=True)


def load_state():
    st = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    st.setdefault("announced", [])
    st.setdefault("reported", [])
    st.setdefault("planned", {})       # vorab in Buffer eingeplante Ankündigungen: gid -> Termin/Halle/Post-ID
    return st


def save_state(st):
    STATE_FILE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")).strip("-")


# --------------------------------------------------------------------------- Handball.net
def fetch_sources():
    """Gibt (Text, Wappen-Map) aller Quellseiten zurück."""
    from bs4 import BeautifulSoup
    texts, crests = [], {}
    for url in SOURCES:
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
        except Exception as e:  # eine Quelle darf ausfallen
            log(f"WARN: {url} nicht ladbar: {e}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for img in soup.find_all("img"):
            src, alt = img.get("src") or "", (img.get("alt") or "").strip()
            if "afiliacion_clubs" in src and alt:
                crests[alt.upper()] = src
        for t in soup(["script", "style", "noscript", "svg"]):
            t.decompose()
        lines = [l for l in soup.get_text("\n", strip=True).split("\n") if l]
        keep = set()
        for i, l in enumerate(lines):          # nur Umgebung unseres Teams behalten
            if TEAM_MATCH.upper() in l.upper():
                keep.update(range(max(0, i - 14), min(len(lines), i + 14)))
        part = [lines[i] for i in sorted(keep)] if keep else lines[:400]
        texts.append(f"### QUELLE {url}\n" + "\n".join(part)[:30000])
    return "\n\n".join(texts), crests


def parse_ics(text):
    """Team-Kalender (.ics) von handball.net -> Spieleliste im Format von extract_games()."""
    text = re.sub(r"\r?\n[ \t]", "", text)                      # gefaltete Zeilen zusammensetzen
    unesc = lambda v: v.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    games = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        f = {}
        for line in block.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                f[k.split(";")[0].upper()] = v
        m = re.match(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})", f.get("DTSTART", ""))
        summary = unesc(f.get("SUMMARY", "")).strip()
        score = re.search(r"\s*\((\d+):(\d+)\)$", summary)
        teams = re.split(r"\s+-\s+", summary[:score.start()] if score else summary)
        if not m or len(teams) != 2:
            log(f"WARN: Kalendertermin nicht lesbar: {summary!r}")
            continue
        desc = unesc(f.get("DESCRIPTION", ""))
        nr = re.search(r"Spielnummer\s+(\w+)", desc)
        hall = unesc(f.get("LOCATION", "")).split(",")[0].strip()
        finished = bool(score) or "finalizado" in desc.lower() or "beendet" in desc.lower()
        games.append({
            "id": nr.group(1) if nr else f.get("UID"),
            "home": teams[0].strip(), "away": teams[1].strip(),
            "start": f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}",
            "hall": hall or None,
            "status": "finished" if finished else "upcoming",
            "goals_home": int(score[1]) if score else None,
            "goals_away": int(score[2]) if score else None,
            "halftime": None,
        })
    return games


def fetch_ics():
    """Spiele aus HANDBALL_ICS laden; None, wenn nicht konfiguriert oder nicht ladbar."""
    url = os.getenv("HANDBALL_ICS", "").replace("webcal://", "https://")
    if not url:
        return None
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        games = parse_ics(r.text)
    except Exception as e:
        log(f"WARN: Kalender-Feed nicht ladbar ({e}) – weiche auf die Webseiten aus")
        return None
    return games or None


def export_crests(games, crest_map):
    """Wappen aller Gegner als saubere PNGs nach docs/crests/ + docs/teams.json (für den Ergebnis-Generator)."""
    import io
    from PIL import Image
    outdir = BASE / "docs" / "crests"
    outdir.mkdir(parents=True, exist_ok=True)
    teams = {}
    for g in games:
        for side in ("home", "away"):
            if TEAM_MATCH.upper() not in g[side].upper():
                teams[g[side].upper()] = g[side]
    out, missing = [], []
    for key, raw in sorted(teams.items(), key=lambda kv: nice(kv[1]).lower()):
        entry = {"name": nice(raw), "crest": None}
        url = crest_map.get(key)
        if url:
            try:
                r = requests.get(url, headers=UA, timeout=30)
                r.raise_for_status()
                im = sp.clean_background(Image.open(io.BytesIO(r.content)), raw)
                box = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
                im = im.crop(box) if box else im
                side_len = max(im.size)
                sq = Image.new("RGBA", (side_len, side_len), (0, 0, 0, 0))
                sq.paste(im, ((side_len - im.width) // 2, (side_len - im.height) // 2))
                sq = sq.resize((400, 400), Image.LANCZOS)
                rel = f"crests/{slug(raw)}.png"
                sq.save(BASE / "docs" / rel)
                entry["crest"] = rel
            except Exception as e:
                log(f"WARN: Wappen {raw} nicht ladbar: {e}")
        if not entry["crest"]:
            missing.append(raw)
        out.append(entry)
    (BASE / "docs" / "teams.json").write_text(json.dumps({"teams": out}, indent=2, ensure_ascii=False))
    log(f"{len(out)} Gegner exportiert, ohne Wappen: {missing or 'keine'}")


def claude(system, user, max_tokens=1500):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY fehlt")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": CLAUDE_MODEL, "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=90,
    )
    if not r.ok:
        print(f"Anthropic API {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])


def extract_games(text, now):
    system = ("Du extrahierst Handballspiele aus Seitentext von handball.net. Antworte NUR mit einem JSON-Array, "
              "ohne Erklärung und ohne Markdown. Erfinde nichts; fehlende Angaben = null.")
    user = f"""Heute ist {now.strftime('%A, %d.%m.%Y %H:%M')} (Europe/Berlin).
Relative Angaben wie "Heute"/"Morgen" in konkrete Daten umrechnen.
Extrahiere alle Spiele, an denen eine Mannschaft mit "{TEAM_MATCH}" im Namen beteiligt ist.
Heim ist die zuerst genannte (linke) Mannschaft.
Felder je Spiel:
  id (Spielnummer, z. B. 2627ESHBKRERWC0502, sonst null),
  home, away (Namen exakt wie auf der Seite),
  start (ISO lokal, "YYYY-MM-DDTHH:MM"),
  hall (Hallenname oder null),
  status ("upcoming" | "live" | "finished"),
  goals_home, goals_away (Endstand als Zahlen, nur wenn beendet, sonst null),
  halftime ("11:12" oder null).
Dedupliziere (gleiches Spiel von mehreren Seiten nur einmal).

TEXT:
{text}"""
    raw = claude(system, user, 3000).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    return json.loads(raw)


# --------------------------------------------------------------------------- Texte
def nice(name):
    """'ECKERNFÖRDER MTV' -> 'Eckernförder MTV' (kurze Wörter wie FT/MTV bleiben groß)."""
    if not name.isupper():
        return name
    cap = lambda w: w if len(w) <= 3 else w.capitalize()
    return " ".join("/".join(cap(p) for p in w.split("/")) for w in name.split(" "))


def announcement_caption(g, start):
    wd = sp.WEEKDAYS[start.weekday()].capitalize()
    hall = f"\n📍 {g['hall']}" if g.get("hall") else ""
    return (f"Nächstes Spiel! 🤾‍♀️\n\n"
            f"{nice(g['home'])} – {nice(g['away'])}\n"
            f"🗓 {wd}, {start.strftime('%d.%m.%Y')} · {start.strftime('%H:%M')} Uhr{hall}\n\n"
            f"Kommt vorbei und feuert uns an! 🖤🤍\n\n{HASHTAGS}")


def report_caption(g, start, dry_run):
    we_home = TEAM_MATCH.upper() in g["home"].upper()
    ours, theirs = (g["goals_home"], g["goals_away"]) if we_home else (g["goals_away"], g["goals_home"])
    outcome = "Sieg" if ours > theirs else ("Niederlage" if ours < theirs else "Unentschieden")
    facts = {
        "Heim": nice(g["home"]), "Gast": nice(g["away"]),
        "Endstand": f"{g['goals_home']}:{g['goals_away']}",
        "Halbzeitstand": g.get("halftime"),
        "Ausgang aus Sicht FT Adler Kiel": outcome,
        "Datum": start.strftime("%d.%m.%Y"), "Halle": g.get("hall"),
        "Wettbewerb": "Kreisliga Frauen, Staffel C (Handballverband Schleswig-Holstein)",
    }
    if dry_run and not os.getenv("ANTHROPIC_API_KEY"):
        return f"[Spielbericht-Platzhalter – im echten Lauf schreibt Claude ihn]\n\n{HASHTAGS}"
    system = ("Du schreibst Instagram-Spielberichte für die Handball-Damen des FT Adler Kiel. "
              "Ton: kurz, warm, sportlich, mit Teamgeist, Deutsch, gern 1–3 passende Emojis. 3–6 Sätze. "
              "STRIKTE REGELN: Nutze ausschließlich die gelieferten Fakten. Erfinde keine Spielszenen, "
              "Torschützinnen, Zitate, Tabellenplätze oder Stimmungen. Nenne keine Spielerinnen. "
              "Fehlt der Halbzeitstand, erwähne ihn nicht. Bei Niederlage fair und aufbauend bleiben. "
              "Gib NUR den Caption-Text aus.")
    text = claude(system, json.dumps(facts, ensure_ascii=False), 600).strip()
    return f"{text}\n\n{HASHTAGS}"


# --------------------------------------------------------------------------- Buffer
def gql(query):
    key = os.getenv("BUFFER_API_KEY")
    if not key:
        raise RuntimeError("BUFFER_API_KEY fehlt")
    r = requests.post("https://api.buffer.com", json={"query": query}, timeout=60,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"Buffer-GraphQL-Fehler: {data['errors']}")
    return data["data"]


def find_channel():
    if os.getenv("BUFFER_CHANNEL_ID"):
        return os.environ["BUFFER_CHANNEL_ID"]
    orgs = gql("{ account { organizations { id name } } }")["account"]["organizations"]
    found = []
    for o in orgs:
        chans = gql('query { channels(input: { organizationId: %s }) { id name displayName service } }'
                    % json.dumps(o["id"]))["channels"]
        found += [c for c in chans if (c.get("service") or "").lower() == "instagram"]
    hit = [c for c in found if CHANNEL_HINT in f"{c.get('name', '')} {c.get('displayName', '')}".lower()]
    pick = hit[0] if hit else (found[0] if len(found) == 1 else None)
    if not pick:
        raise RuntimeError(f"Instagram-Kanal nicht eindeutig. Gefunden: {found}")
    return pick["id"]


def build_post_mutation(channel_id, text, image_url, alt, due_utc):
    q = lambda s: json.dumps(s, ensure_ascii=False)
    return f"""mutation CreatePost {{
  createPost(input: {{
    text: {q(text)}
    channelId: {q(channel_id)}
    schedulingType: automatic
    mode: customScheduled
    dueAt: {q(due_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z'))}
    assets: [{{ image: {{ url: {q(image_url)} metadata: {{ altText: {q(alt)} }} }} }}]
    metadata: {{ instagram: {{ type: post, shouldShareToFeed: true }} }}
  }}) {{
    ... on PostActionSuccess {{ post {{ id }} }}
    ... on MutationError {{ message }}
  }}
}}"""


def create_post(channel_id, text, image_url, alt, due=None):
    due = due or datetime.now(timezone.utc) + timedelta(minutes=5)
    res = gql(build_post_mutation(channel_id, text, image_url, alt, due))["createPost"]
    if "post" not in res:
        raise RuntimeError(f"Buffer lehnt ab: {res.get('message')}")
    return res["post"]["id"]


# --------------------------------------------------------------------------- Bild-Hosting (GitHub raw)
def host_image(path):
    """Bild ins Repo pushen und öffentliche Raw-URL zurückgeben (Buffer braucht eine öffentliche URL)."""
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.getenv("GITHUB_REF_NAME", "main")
    rel = path.relative_to(BASE).as_posix()
    run = lambda *c: subprocess.run(c, check=True, cwd=BASE)
    run("git", "config", "user.name", "adler-autopilot")
    run("git", "config", "user.email", "autopilot@users.noreply.github.com")
    run("git", "add", rel)
    run("git", "commit", "-m", f"Sharepic {path.name}")
    run("git", "pull", "--rebase", "--autostash")
    run("git", "push")
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{rel}"
    for _ in range(18):                              # bis zu ~90 s auf das CDN warten
        if requests.head(url, timeout=15).status_code == 200:
            return url
        time.sleep(5)
    raise RuntimeError(f"Bild-URL nicht erreichbar: {url} (Repo muss öffentlich sein)")


def commit_state(st, label):
    save_state(st)
    subprocess.run(["git", "add", "state.json"], cwd=BASE, check=True)
    subprocess.run(["git", "commit", "-m", label], cwd=BASE, check=False)
    subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=BASE, check=False)
    subprocess.run(["git", "push"], cwd=BASE, check=False)


# --------------------------------------------------------------------------- Hauptlogik
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", help="JSON-Datei mit Spielen statt Handball.net (Test)")
    ap.add_argument("--now", help="Zeitpunkt simulieren, z. B. 2026-10-29T13:00")
    ap.add_argument("--export-crests", action="store_true",
                    help="Gegner-Wappen und Teamliste für den Generator nach docs/ schreiben")
    ap.add_argument("--plan-season", action="store_true",
                    help="alle offenen Ankündigungen vorab in Buffer einplanen (statt Ergebnisse/fällige Posts)")
    a = ap.parse_args()

    now = datetime.fromisoformat(a.now).replace(tzinfo=TZ) if a.now else datetime.now(TZ)
    st = load_state()
    OUT_DIR.mkdir(exist_ok=True)
    POSTS_DIR.mkdir(exist_ok=True)

    crest_map = {}
    if a.fixture:
        games = json.loads(Path(a.fixture).read_text())
    else:
        text, crest_map = fetch_sources()             # Webseiten: Wappen-URLs (und Rückfall für die Spiele)
        games = fetch_ics()
        if games is None and not text.strip():
            sys.exit("Keine Seite ladbar – Abbruch (nichts gepostet).")
        if games is None:
            games = extract_games(text, now)
        else:
            log("Spiele aus dem Kalender-Feed gelesen")
    log(f"{len(games)} Spiel(e) erkannt")
    if a.export_crests:
        export_crests(games, crest_map)
        return
    if a.dry_run:
        (OUT_DIR / "games.json").write_text(json.dumps(games, indent=2, ensure_ascii=False))

    channel = None
    todo = []
    alerts = []
    for g in games:
        if not g.get("start") or not g.get("home") or not g.get("away"):
            continue
        start = datetime.fromisoformat(g["start"]).replace(tzinfo=TZ)
        gid = g.get("id") or f"{g['start']}-{slug(g['home'])}-{slug(g['away'])}"
        days_to = (start.date() - now.date()).days
        planned = st["planned"].get(gid)

        if planned and g.get("status") == "upcoming" and start > now:      # Termin/Halle seit der Planung geändert?
            seen = [g["start"], g.get("hall") or ""]
            if seen != [planned["start"], planned.get("hall") or ""] and planned.get("alerted") != seen:
                alerts.append(f"{nice(g['home'])} – {nice(g['away'])}: geplant {planned['start']} / {planned.get('hall') or '-'}, "
                              f"jetzt {seen[0]} / {seen[1] or '-'} – Buffer-Post {planned['post_id']} bitte prüfen/anpassen")
                planned["alerted"] = seen

        if a.plan_season:
            if g.get("status") == "upcoming" and start > now and gid not in st["announced"] and not planned:
                todo.append(("ankuendigung", gid, g, start))
            continue
        if (g.get("status") == "upcoming" and start > now and days_to <= 3
                and (days_to < 3 or now.hour >= 12) and gid not in st["announced"] and not planned):
            todo.append(("ankuendigung", gid, g, start))
        if (g.get("status") == "finished" and g.get("goals_home") is not None and g.get("goals_away") is not None
                and timedelta(0) < now - start < timedelta(days=3) and gid not in st["reported"]):
            todo.append(("ergebnis", gid, g, start))

    log(f"{len(todo)} Posting(s) fällig")
    for kind, gid, g, start in todo:
        for side in ("home", "away"):                # Wappen-URL aus dem HTML der Seite
            g.setdefault(f"{side}_crest_url", crest_map.get(g[side].upper()))
        fname = f"{start.strftime('%Y-%m-%d')}_{kind}_{slug(g['away'] if TEAM_MATCH.upper() in g['home'].upper() else g['home'])}.png"
        path = (OUT_DIR if a.dry_run else POSTS_DIR) / fname
        game = dict(home=g["home"], away=g["away"], home_crest_url=g.get("home_crest_url"),
                    away_crest_url=g.get("away_crest_url"))
        if kind == "ankuendigung":
            game.update(date=start, hall=g.get("hall") or "")
            sp.announcement(game, str(path), DESIGN, "4:5")
            if a.dry_run:                            # Vorschau zusätzlich als Story (9:16)
                sp.announcement(game, str(path.with_name(path.stem + "_story.png")), DESIGN, "9:16")
            caption = announcement_caption(g, start)
            alt = f"Ankündigung: {nice(g['home'])} gegen {nice(g['away'])} am {start.strftime('%d.%m.%Y')} um {start.strftime('%H:%M')} Uhr"
        else:
            game.update(goals_home=g["goals_home"], goals_away=g["goals_away"], halftime=g.get("halftime"),
                        us_home=TEAM_MATCH.upper() in g["home"].upper())
            sp.result(game, str(path), DESIGN, "4:5")
            if a.dry_run:
                sp.result(game, str(path.with_name(path.stem + "_story.png")), DESIGN, "9:16")
            caption = report_caption(g, start, a.dry_run)
            alt = f"Ergebnis: {nice(g['home'])} {g['goals_home']}:{g['goals_away']} {nice(g['away'])}"
        log(f"[{kind}] {gid}: Bild {path.name}")

        due = None                                    # None = sofort (in 5 Minuten)
        if a.plan_season:
            due = datetime.combine(start.date() - timedelta(days=ANNOUNCE_DAYS), dtime(ANNOUNCE_HOUR), TZ)
            due = (due if due > now + timedelta(minutes=5) else now + timedelta(minutes=5)).astimezone(timezone.utc)

        if a.dry_run:
            payload = build_post_mutation("CHANNEL_ID", caption, f"https://raw.githubusercontent.com/OWNER/REPO/main/posts/{fname}",
                                          alt, due or now.astimezone(timezone.utc) + timedelta(minutes=5))
            (OUT_DIR / f"{path.stem}.caption.txt").write_text(caption)
            (OUT_DIR / f"{path.stem}.mutation.graphql").write_text(payload)
            log("  --- Caption ---\n" + caption + "\n  ---------------")
            if due:
                log(f"  Veröffentlichung geplant: {due.astimezone(TZ).strftime('%a %d.%m.%Y %H:%M')} Uhr")
            continue

        channel = channel or find_channel()
        url = host_image(path)
        post_id = create_post(channel, caption, url, alt, due)
        log(f"  -> Buffer-Post {post_id}" + (f" für {due.astimezone(TZ).strftime('%d.%m.%Y %H:%M')} Uhr geplant" if due else ""))
        if a.plan_season:
            st["planned"][gid] = {"post_id": post_id, "start": g["start"], "hall": g.get("hall") or "",
                                  "due": due.isoformat()}
        else:
            st["announced" if kind == "ankuendigung" else "reported"].append(gid)
        commit_state(st, f"State {kind} {gid}")

    for msg in alerts:
        log("ACHTUNG Spielverlegung: " + msg)
    if alerts and not a.dry_run:
        commit_state(st, "State: Verlegung gemeldet")
        sys.exit("Spieltermin/Halle hat sich geändert (siehe oben) – Lauf bewusst als fehlgeschlagen markiert, "
                 "damit GitHub eine Benachrichtigung schickt.")


if __name__ == "__main__":
    main()
