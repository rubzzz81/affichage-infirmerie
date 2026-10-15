#!/usr/bin/env python3
"""Génère de FAUSSES statistiques pour présenter la solution (démonstration, formation).

  python3 tools/demo-data.py DOSSIER [--days 60] [--seed 1]

DOSSIER est une copie de l'application (avec son config.json) : le script y écrit un data.db
fictif, d'après les infirmières et les horaires du config.json. Les données sont plausibles
mais inventées : ne jamais le lancer dans le dossier de production (le script refuse si un
data.db existe déjà, sauf avec --force).

Exemple (instance de démonstration à côté de la vraie, sans la toucher) :
  cp -r cabinet cabinet-demo && rm -f cabinet-demo/data.db cabinet-demo/state.json
  # éditer cabinet-demo/config.json : "port": 8090, "tlsPort"/ports différents, codes à définir
  python3 tools/demo-data.py cabinet-demo
"""
import argparse
import datetime
import json
import os
import random
import sqlite3
import sys

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def hours_for(nurse, day):
    h = nurse.get("hours", {})
    return h.get(DAYS[day.weekday()]) or h.get("default") or ["08:00", "17:00"]


def at(day, hhmm, jitter, rnd):
    h, m = map(int, hhmm.split(":"))
    t = datetime.datetime.combine(day, datetime.time(h, m)) + datetime.timedelta(seconds=rnd.randint(-jitter, jitter))
    return int(t.timestamp())


def make_day(nurse, day, rnd, busy):
    """Liste d'intervalles (état, début, fin) pour une journée de travail."""
    start_s, end_s = hours_for(nurse, day)
    t = at(day, start_s, 300, rnd)
    end = at(day, end_s, 600, rnd)
    # la matinée de rentrée / veille de vacances est plus chargée : `busy` module la fréquence
    evs, cur = [], t
    lunch = at(day, "12:00", 900, rnd)
    lunch_done = end - t < 6 * 3600           # demi-journée : pas de pause repas
    while cur < end - 600:
        if not lunch_done and cur >= lunch:
            d = rnd.randint(35, 55) * 60
            evs.append(("pause", cur, min(cur + d, end)))
            cur += d
            lunch_done = True
            continue
        r = rnd.random()
        if r < 0.32 * busy:
            d = rnd.choice([8, 10, 12, 15, 15, 20, 20, 25, 30, 45]) * 60 + rnd.randint(0, 59)
            evs.append(("consult", cur, min(cur + d, end)))
        elif r < 0.62:
            d = rnd.randint(5, 20) * 60
            evs.append(("pause", cur, min(cur + d, end)))
        else:
            d = rnd.randint(4, 25) * 60
            evs.append(("present", cur, min(cur + d, end)))
        cur += d
    return evs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dossier")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--merge", action="store_true", help="ajoute les faux événements à un data.db EXISTANT (production), "
                    "avec des identifiants négatifs et seulement avant le premier vrai événement")
    ap.add_argument("--remove", action="store_true", help="supprime les faux événements ajoutés par --merge")
    a = ap.parse_args()

    cfg_path = os.path.join(a.dossier, "config.json")
    db_path = os.path.join(a.dossier, "data.db")
    if not os.path.isfile(cfg_path):
        sys.exit("config.json introuvable dans " + a.dossier)
    if a.remove:
        db = sqlite3.connect(db_path, timeout=30)
        n = db.execute("DELETE FROM events WHERE id < 0").rowcount
        db.commit()
        db.close()
        sys.exit("%d faux événements supprimés (les vrais n'ont pas été touchés)." % n)
    if os.path.exists(db_path) and not a.force and not a.merge:
        sys.exit("%s existe déjà : refus d'écraser de vraies données (--force pour remplacer)." % db_path)
    nurses = json.load(open(cfg_path, encoding="utf-8"))["nurses"]

    rnd = random.Random(a.seed)
    first_real = None
    if a.merge:
        db = sqlite3.connect(db_path, timeout=30)
        db.execute("DELETE FROM events WHERE id < 0")                      # idempotent
        row = db.execute("SELECT MIN(start) FROM events").fetchone()
        first_real = row[0] if row and row[0] else None
    else:
        if os.path.exists(db_path):
            os.remove(db_path)
        db = sqlite3.connect(db_path)
    next_id = -1
    if not a.merge:
        db.executescript("""
        CREATE TABLE events(id INTEGER PRIMARY KEY, nurse TEXT NOT NULL, status TEXT NOT NULL,
            start INTEGER NOT NULL, "end" INTEGER, auto INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX events_start ON events(start);
        CREATE TABLE meta(k TEXT PRIMARY KEY, v INTEGER);""")
    today = datetime.date.today()
    n = 0
    for back in range(a.days, 0, -1):
        day = today - datetime.timedelta(days=back)
        if day.weekday() >= 5:
            continue
        if first_real and int(datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(0)).timestamp()) > first_real:
            continue                          # jamais de faux événement à côté de vrais
        for nurse in nurses:
            if rnd.random() < 0.06:           # absence (maladie, formation) : aucune activité ce jour
                continue
            busy = rnd.uniform(0.6, 1.5)
            evs = make_day(nurse, day, rnd, busy)
            for status, s, e in evs:
                db.execute('INSERT INTO events(id, nurse, status, start, "end", auto) VALUES(?,?,?,?,?,0)',
                           (next_id, nurse["id"], status, s, e)); next_id -= 1
                n += 1
            if evs:                           # fin de journée automatique (absente), comme le vrai serveur
                db.execute('INSERT INTO events(id, nurse, status, start, "end", auto) VALUES(?,?,?,?,?,1)',
                           (next_id, nurse["id"], "absent", evs[-1][2], evs[-1][2] + 3600 * 10)); next_id -= 1
                n += 1
    if not a.merge:
        db.execute("INSERT INTO meta VALUES('last_seen', ?)", (int(datetime.datetime.now().timestamp()),))
    db.commit()
    db.close()
    print("%d événements fictifs sur %d jours pour %d infirmière(s) : %s" % (n, a.days, len(nurses), db_path))
    print("Rappel : ces statistiques sont INVENTÉES, à n'utiliser que pour une démonstration.")


if __name__ == "__main__":
    main()
