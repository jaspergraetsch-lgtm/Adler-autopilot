#!/usr/bin/env python3
"""Kommandozeile für die Sharepics (läuft lokal, z. B. durch Claude Cowork – kein Server nötig).

  python3 render.py ankuendigung --heim "Eckernförder MTV" --gast "FT Adler Kiel" \
      --datum 2026-10-31 --zeit 17:00 --halle "Wulfsteerthalle" \
      --heim-wappen <URL> --gast-wappen <URL> --out ankuendigung.png

  python3 render.py ergebnis --heim ... --gast ... --tore-heim 22 --tore-gast 25 \
      --halbzeit 11:12 --wir-heim nein --heim-wappen <URL> --gast-wappen <URL> --out ergebnis.png
"""
import argparse
from datetime import datetime
import sharepics as sp

p = argparse.ArgumentParser()
p.add_argument("modus", choices=["ankuendigung", "ergebnis"])
p.add_argument("--heim", required=True); p.add_argument("--gast", required=True)
p.add_argument("--heim-wappen"); p.add_argument("--gast-wappen")
p.add_argument("--datum"); p.add_argument("--zeit"); p.add_argument("--halle", default="")
p.add_argument("--tore-heim", type=int); p.add_argument("--tore-gast", type=int)
p.add_argument("--halbzeit"); p.add_argument("--wir-heim", default="nein")
p.add_argument("--design", choices=["dunkel", "linien", "hell"], default="dunkel")
p.add_argument("--format", choices=["4:5", "9:16"], default="4:5")
p.add_argument("--out", required=True)
a = p.parse_args()

g = dict(home=a.heim, away=a.gast, home_crest_url=a.heim_wappen, away_crest_url=a.gast_wappen)
if a.modus == "ankuendigung":
    g.update(date=datetime.strptime(f"{a.datum} {a.zeit}", "%Y-%m-%d %H:%M"), hall=a.halle)
    sp.announcement(g, a.out, a.design, a.format)
else:
    g.update(goals_home=a.tore_heim, goals_away=a.tore_gast, halftime=a.halbzeit,
             us_home=a.wir_heim.lower() in ("ja", "true", "1"))
    sp.result(g, a.out, a.design, a.format)
print("OK", a.out)
