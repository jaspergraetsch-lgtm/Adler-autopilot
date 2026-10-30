# FT Adler Kiel – Posting-Autopilot (ohne Server)

Läuft kostenlos in GitHub Actions: liest handball.net, baut die Sharepics, schreibt den Spielbericht (Claude) und plant die Posts über Buffer auf Instagram `adlerhandballmaedels` ein.

## Einrichten (einmalig, ca. 5 Minuten)
1. Neues **öffentliches** GitHub-Repo anlegen (Buffer braucht öffentliche Bild-URLs) und den Inhalt dieses Ordners hochladen (alle Dateien liegen flach im Hauptordner). Zusätzlich die Datei `adler.yml` unter dem Pfad `.github/workflows/adler.yml` neu anlegen (Add file → Create new file, Pfad in das Namensfeld tippen, Inhalt einfügen).
2. Repo → Settings → Secrets and variables → Actions → zwei Secrets anlegen:
   - `BUFFER_API_KEY` (Buffer: publish.buffer.com/settings/api → Generate API key)
   - `ANTHROPIC_API_KEY` (console.anthropic.com → API keys; Kosten ca. wenige Cent pro Monat)
3. Actions-Tab → „Adler Autopilot“ → **Run workflow** (Haken „Nur testen“ lassen). Im Log siehst du erkannte Spiele, Captions, Bilder.
   **Saison vorab planen:** Run workflow mit Haken „Saison planen“ (zuerst mit „Nur testen“, dann ohne) plant alle offenen Ankündigungen mit Datum in Buffer ein, standardmäßig 3 Tage vor dem Spiel um 12 Uhr (`ANNOUNCE_DAYS`, `ANNOUNCE_HOUR`). Du siehst und änderst sie im Buffer-Kalender. Der stündliche Lauf postet dann nur noch Ergebnisse (und Ankündigungen für später neu angesetzte Spiele) und markiert den Lauf als fehlgeschlagen – GitHub schickt dir dann eine Mail –, wenn sich Uhrzeit oder Halle eines geplanten Spiels geändert haben; den Buffer-Post musst du dann dort anpassen.
4. Passt alles: noch einmal ausführen mit **entferntem** Haken. Ab dann läuft es stündlich von allein.

## Einstellungen (optional, als Repo-Variablen/Env im Workflow)
`DESIGN` = dunkel | hell | linien · `HASHTAGS` · `BUFFER_CHANNEL` · `HANDBALL_URLS` (neue Saison: season_id anpassen)

## Spielplan-Quelle
Der Bot liest den Team-Kalender von handball.net (`HANDBALL_ICS`, im Workflow gesetzt; die Adresse steht unter „Termin abonnieren“, `webcal://` = `https://`). Daraus kommen alle Spiele der Saison mit Spielnummer, Halle und Ergebnis. Nur wenn der Feed nicht ladbar ist, liest Claude die Teamseiten als Rückfall. Hinweis: Der Feed kann Ergebnisse etwas verzögert liefern.

Läufe mit „Nur testen“ legen die erzeugten Grafiken als Download **vorschau** an den Lauf (Actions → Lauf → Artifacts).

## Hinweise
- Ankündigung: ab 3 Tage vor Anpfiff (ab 12 Uhr). Ergebnis: sobald der Endstand auf handball.net steht.
- Doppelposts verhindert `state.json`.
- Bei nicht erkannten Spielen/Layoutänderungen: nichts wird gepostet, der Lauf meldet es im Log.
