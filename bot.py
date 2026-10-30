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
import email
import email.header
import email.utils
import hashlib
import imaplib
import json
import os
import re
import smtplib
import subprocess
import sys
import time
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
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
MAIL_USER = os.getenv("MAIL_USER")                                 # Postfach des Bots (Absender + Antworten)
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
MAIL_SMTP_HOST = os.getenv("MAIL_SMTP_HOST")
MAIL_SMTP_PORT = int(os.getenv("MAIL_SMTP_PORT") or 465)           # 465 = SSL, 587 = STARTTLS
MAIL_IMAP_HOST = os.getenv("MAIL_IMAP_HOST")
APPROVERS = [a.strip().lower() for a in (os.getenv("MAIL_APPROVERS") or "").split(",") if a.strip()]
UA = {"User-Agent": "Mozilla/5.0 (compatible; AdlerAutopilot/1.0)"}


# --------------------------------------------------------------------------- Hilfen
def log(*a):
    print(*a, flush=True)


def load_state():
    st = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    st.setdefault("announced", [])
    st.setdefault("reported", [])
    st.setdefault("pending", {})       # auf Freigabe wartende Postings
    st.setdefault("tg_offset", 0)      # bis wohin die Telegram-Antworten verarbeitet sind
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
    return " ".join(w if len(w) <= 3 else w.capitalize() for w in name.split(" "))


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


def create_post(channel_id, text, image_url, alt):
    due = datetime.now(timezone.utc) + timedelta(minutes=5)
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


# --------------------------------------------------------------------------- Freigabe per Mail
APPROVE_WORDS = {"ok", "okay", "ja", "freigeben", "freigabe", "passt", "gut", "super"}
REJECT_WORDS = {"nein", "ablehnen", "abgelehnt", "stop", "stopp", "no"}
CHANGE_WORDS = {"ändern", "aendern", "korrigieren", "korrektur", "änderung"}
MAX_CAPTION = 2200                                                  # Instagram-Limit
REPLY_HEADER = re.compile(r"^(am .+ schrieb|on .+ wrote|von:|from:|-{3,}|_{3,})", re.I)
KIND_LABEL = {"ankuendigung": "Ankündigung", "ergebnis": "Ergebnis"}


def approval_configured():
    return all([MAIL_USER, MAIL_PASSWORD, MAIL_SMTP_HOST, MAIL_IMAP_HOST, APPROVERS])


def pending_id(kind, gid):
    return hashlib.sha1(f"{kind}:{gid}".encode()).hexdigest()[:10]


def send_mail(subject, body, attachment=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = MAIL_USER, ", ".join(APPROVERS), subject
    m.set_content(body)
    if attachment:
        m.add_attachment(Path(attachment).read_bytes(), maintype="image", subtype="png",
                         filename=Path(attachment).name)
    if MAIL_SMTP_PORT == 587:
        with smtplib.SMTP(MAIL_SMTP_HOST, MAIL_SMTP_PORT, timeout=60) as s:
            s.starttls()
            s.login(MAIL_USER, MAIL_PASSWORD)
            s.send_message(m)
    else:
        with smtplib.SMTP_SSL(MAIL_SMTP_HOST, MAIL_SMTP_PORT, timeout=60) as s:
            s.login(MAIL_USER, MAIL_PASSWORD)
            s.send_message(m)


def request_approval(pid, rev, kind, path, caption):
    again = f" – korrigierte Fassung {rev}" if rev else ""
    send_mail(
        f"Freigabe [adler-{pid}.{rev}] {KIND_LABEL[kind]}{again}",
        f"Neues Instagram-Posting für FT Adler Kiel ({KIND_LABEL[kind]}{again}).\n"
        f"Das Bild hängt an dieser Mail.\n\n--- Text ---\n{caption}\n------------\n\n"
        "Bitte ANTWORTE auf diese Mail, die erste Zeile entscheidet:\n"
        "  OK       – das Posting wird veröffentlicht\n"
        "  NEIN     – es wird nichts gepostet\n"
        "  ÄNDERN   – darunter den kompletten neuen Text schreiben; du bekommst ihn\n"
        "             zur Kontrolle noch einmal zugeschickt und bestätigst dann mit OK\n"
        "Ohne Antwort wird nichts gepostet.",
        attachment=path)


def mail_text(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/plain":
            return (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
    return ""


def parse_reply(text):
    """Liefert (erstes Wort, Rest) der Antwort ohne zitierten Mailverlauf."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or REPLY_HEADER.match(stripped):
            if lines:
                break                                 # ab hier beginnt der zitierte Verlauf
            continue                                  # Antwort steht unter dem Zitat
        lines.append(line.rstrip())
    m = re.match(r"([A-Za-zÄÖÜäöüß]+)[ \t]*:?[ \t]*(.*)", "\n".join(lines).strip(), re.S)
    return (m.group(1).lower(), m.group(2).strip()) if m else ("", "")


def read_replies(handle):
    """Ungelesene Antworten auf Freigabe-Mails lesen. handle(pid, rev, decision, text, sender) -> True, wenn erledigt."""
    with imaplib.IMAP4_SSL(MAIL_IMAP_HOST) as im:
        im.login(MAIL_USER, MAIL_PASSWORD)
        im.select("INBOX")
        _, data = im.search(None, "UNSEEN", "SUBJECT", '"[adler-"')
        for num in data[0].split():
            _, parts = im.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(parts[0][1])
            sender = email.utils.parseaddr(msg.get("From", ""))[1].lower()
            m = re.search(r"\[adler-([0-9a-f]{10})(?:\.(\d+))?\]", str(email.header.make_header(email.header.decode_header(msg.get("Subject", "")))))
            if not m or sender not in APPROVERS:
                continue                              # fremde Absender werden ignoriert und bleiben ungelesen
            word, rest = parse_reply(mail_text(msg))
            decision = ("ok" if word in APPROVE_WORDS else "nein" if word in REJECT_WORDS
                        else "aendern" if word in CHANGE_WORDS else None)
            if handle(m.group(1), int(m.group(2) or 0), decision, rest, sender):
                im.store(num, "+FLAGS", "\\Seen")


def commit_state(st, label):
    save_state(st)
    subprocess.run(["git", "add", "state.json"], cwd=BASE, check=True)
    subprocess.run(["git", "commit", "-m", label], cwd=BASE, check=False)
    subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=BASE, check=False)
    subprocess.run(["git", "push"], cwd=BASE, check=False)


def finish_pending(st, pid):
    p = st["pending"].pop(pid)
    st["announced" if p["kind"] == "ankuendigung" else "reported"].append(p["gid"])
    return p


def process_approvals(st, now):
    """Abgelaufene Entwürfe verwerfen, dann Antworten der Prüferinnen umsetzen."""
    for pid, p in list(st["pending"].items()):
        start = datetime.fromisoformat(p["start"]).replace(tzinfo=TZ)
        if (start <= now) if p["kind"] == "ankuendigung" else (now - start > timedelta(days=3)):
            finish_pending(st, pid)
            log(f"[{p['kind']}] {p['gid']}: abgelaufen ohne Freigabe – nicht gepostet")
            commit_state(st, f"State: {pid} abgelaufen")

    def handle(pid, rev, decision, text, sender):
        p = st["pending"].get(pid)
        if not p:
            return True                               # schon erledigt (z. B. zweite Antwort)
        if rev != p.get("rev", 0):
            send_mail(f"Veraltete Freigabe-Antwort {KIND_LABEL[p['kind']]}",
                      f"Die Antwort von {sender} bezog sich auf eine ältere Fassung und wurde ignoriert. "
                      "Bitte auf die neueste Freigabe-Mail antworten.")
            return True
        if decision is None:
            send_mail(f"Antwort nicht verstanden – {KIND_LABEL[p['kind']]}",
                      "Ich habe die Antwort nicht verstanden. Bitte antworte auf die Freigabe-Mail mit OK, NEIN oder "
                      "ÄNDERN (mit neuem Text darunter) in der ersten Zeile.")
            return True
        if decision == "aendern":
            if not text or len(text) > MAX_CAPTION:
                send_mail(f"Änderung nicht übernommen – {KIND_LABEL[p['kind']]}",
                          f"Unter ÄNDERN muss der neue Text stehen (maximal {MAX_CAPTION} Zeichen). "
                          "Es wurde nichts geändert und nichts gepostet.")
                return True
            p["caption"], p["rev"] = text, p.get("rev", 0) + 1
            request_approval(pid, p["rev"], p["kind"], POSTS_DIR / p["fname"], text)
            commit_state(st, f"State {p['kind']} {p['gid']}: Text geändert von {sender}")
            log(f"[{p['kind']}] {p['gid']}: Text geändert von {sender} – erneut zur Freigabe gesendet")
            return True
        if decision == "ok":
            post_id = create_post(find_channel(), p["caption"], p["image_url"], p["alt"])
            log(f"[{p['kind']}] {p['gid']}: freigegeben von {sender} -> Buffer-Post {post_id}")
            result = "freigegeben und an Buffer übergeben"
        else:
            log(f"[{p['kind']}] {p['gid']}: abgelehnt von {sender} – nicht gepostet")
            result = "abgelehnt, es wird nichts gepostet"
        finish_pending(st, pid)
        commit_state(st, f"State {p['kind']} {p['gid']}")
        send_mail(f"Erledigt: {KIND_LABEL[p['kind']]}", f"Das Posting wurde {result} (Antwort von {sender}).")
        return True

    read_replies(handle)


# --------------------------------------------------------------------------- Hauptlogik
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", help="JSON-Datei mit Spielen statt Handball.net (Test)")
    ap.add_argument("--now", help="Zeitpunkt simulieren, z. B. 2026-10-29T13:00")
    a = ap.parse_args()

    now = datetime.fromisoformat(a.now).replace(tzinfo=TZ) if a.now else datetime.now(TZ)
    st = load_state()
    if st["pending"] and not a.dry_run:
        if not approval_configured():
            sys.exit("Es warten Postings auf Freigabe, aber die Mail-Konfiguration fehlt – Abbruch (nichts gepostet).")
        process_approvals(st, now)
    OUT_DIR.mkdir(exist_ok=True)
    POSTS_DIR.mkdir(exist_ok=True)

    crest_map = {}
    if a.fixture:
        games = json.loads(Path(a.fixture).read_text())
    else:
        text, crest_map = fetch_sources()
        if not text.strip():
            sys.exit("Keine Seite ladbar – Abbruch (nichts gepostet).")
        games = extract_games(text, now)
    log(f"{len(games)} Spiel(e) erkannt")
    if a.dry_run:
        (OUT_DIR / "games.json").write_text(json.dumps(games, indent=2, ensure_ascii=False))

    todo = []
    for g in games:
        if not g.get("start") or not g.get("home") or not g.get("away"):
            continue
        start = datetime.fromisoformat(g["start"]).replace(tzinfo=TZ)
        gid = g.get("id") or f"{g['start']}-{slug(g['home'])}-{slug(g['away'])}"
        days_to = (start.date() - now.date()).days

        if (g.get("status") == "upcoming" and start > now and days_to <= 3
                and (days_to < 3 or now.hour >= 12) and gid not in st["announced"]
                and pending_id("ankuendigung", gid) not in st["pending"]):
            todo.append(("ankuendigung", gid, g, start))
        if (g.get("status") == "finished" and g.get("goals_home") is not None and g.get("goals_away") is not None
                and timedelta(0) < now - start < timedelta(days=3) and gid not in st["reported"]
                and pending_id("ergebnis", gid) not in st["pending"]):
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
            caption = announcement_caption(g, start)
            alt = f"Ankündigung: {nice(g['home'])} gegen {nice(g['away'])} am {start.strftime('%d.%m.%Y')} um {start.strftime('%H:%M')} Uhr"
        else:
            game.update(goals_home=g["goals_home"], goals_away=g["goals_away"], halftime=g.get("halftime"),
                        us_home=TEAM_MATCH.upper() in g["home"].upper())
            sp.result(game, str(path), DESIGN, "4:5")
            caption = report_caption(g, start, a.dry_run)
            alt = f"Ergebnis: {nice(g['home'])} {g['goals_home']}:{g['goals_away']} {nice(g['away'])}"
        log(f"[{kind}] {gid}: Bild {path.name}")

        if a.dry_run:
            payload = build_post_mutation("CHANNEL_ID", caption, f"https://raw.githubusercontent.com/OWNER/REPO/main/posts/{fname}",
                                          alt, now.astimezone(timezone.utc) + timedelta(minutes=5))
            (OUT_DIR / f"{path.stem}.caption.txt").write_text(caption)
            (OUT_DIR / f"{path.stem}.mutation.graphql").write_text(payload)
            log("  --- Caption ---\n" + caption + "\n  ---------------")
            log("  (im echten Lauf: Freigabe-Mail an die Prüferinnen, Posting erst nach OK)")
            continue

        if not approval_configured():
            sys.exit("Mail-Freigabe nicht konfiguriert (MAIL_USER, MAIL_PASSWORD, MAIL_SMTP_HOST, MAIL_IMAP_HOST, "
                     "MAIL_APPROVERS) – Abbruch, es wird nichts ohne Freigabe gepostet.")
        pid = pending_id(kind, gid)
        url = host_image(path)
        request_approval(pid, 0, kind, path, caption)
        st["pending"][pid] = dict(kind=kind, gid=gid, start=start.isoformat(), caption=caption, alt=alt,
                                  image_url=url, fname=path.name, rev=0)
        commit_state(st, f"State: {kind} {gid} wartet auf Freigabe")
        log(f"  -> zur Freigabe an {', '.join(APPROVERS)} gesendet ({pid})")


if __name__ == "__main__":
    main()
