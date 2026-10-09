#!/usr/bin/env python3
"""Affichage de présence des cabinets d'infirmières.

Serveur sans dépendance (bibliothèque standard uniquement).
  /            page d'affichage (écran du couloir, mode kiosque)
  /admin       page de commande des infirmières (téléphone / PC)
  /stats       statistiques d'activité (consultations, occupation...)
  /logo        logo de l'établissement (défini dans config.json)
  /api/state   état courant (JSON)
  /api/events  flux temps réel (Server-Sent Events)
  /api/set     modification d'un état (POST JSON)
  /api/stats, /api/stats.csv, /api/events.csv   monitoring (JSON / CSV)
"""
import csv
import datetime
import hmac
import io
import json
import mimetypes
import os
import queue
import re
import sqlite3
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
STATE_FILE = os.path.join(BASE, "state.json")
DB_FILE = os.path.join(BASE, "data.db")
DEFAULT_LOGO = os.path.join(STATIC, "logo-default.svg")
STATUSES = ("present", "absent", "consult", "pause")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_RANGE_DAYS = 800

with open(os.path.join(BASE, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)
NURSES = {n["id"]: n for n in CONFIG["nurses"]}
MIN_EVENT_SECONDS = int(CONFIG.get("minEventSeconds", 10))

lock = threading.Lock()
subscribers = set()
state = {"day": "", "nurses": {}}
db = None


# --------------------------------------------------------------------------- état

def save_state():
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state.update(json.load(f))
    except (OSError, ValueError):
        pass


def roll_day():
    """Chaque nouveau jour : tout le monde repasse en 'absente', horaires par défaut."""
    today = datetime.date.today().isoformat()
    if state.get("day") == today:
        return False
    state["day"] = today
    state["nurses"] = {nid: {"status": "absent"} for nid in NURSES}
    for nid in NURSES:
        record(nid, "absent", auto=True)
    save_state()
    return True


def default_hours(nurse, date):
    hours = nurse.get("hours", {})
    return hours.get(DAYS[date.weekday()]) or hours.get("default") or ["08:00", "17:00"]


def logo_path():
    rel = CONFIG.get("logo")
    path = os.path.normpath(os.path.join(BASE, rel)) if rel else ""
    return path if path and os.path.isfile(path) else DEFAULT_LOGO


def snapshot():
    today = datetime.date.today()
    out = []
    for nid, nurse in NURSES.items():
        s = state["nurses"].get(nid, {})
        start, end = s.get("hours") or default_hours(nurse, today)
        out.append({
            "id": nid,
            "name": nurse["name"],
            "status": s.get("status", "absent"),
            "back": s.get("back"),
            "start": start,
            "end": end,
            "customHours": bool(s.get("hours")),
            "updated": s.get("updated"),
        })
    return {"school": CONFIG.get("school", ""),
            "title": CONFIG.get("title", "Cabinet d'infirmières"),
            "logo": "/logo?v=%d" % int(os.path.getmtime(logo_path())),
            "pinRequired": bool(CONFIG.get("pin")),
            "nurses": out}


def broadcast():
    data = json.dumps(snapshot(), ensure_ascii=False)
    for q in list(subscribers):
        q.put(data)


def check_pin(supplied):
    pin = str(CONFIG.get("pin") or "")
    return not pin or hmac.compare_digest(pin, str(supplied or ""))


def apply_change(body):
    """Retourne (code HTTP, message)."""
    if not check_pin(body.get("pin")):
        return 403, "Code incorrect"
    nid = body.get("id")
    if nid not in NURSES:
        return 400, "Infirmière inconnue"
    with lock:
        roll_day()
        s = state["nurses"].setdefault(nid, {"status": "absent"})
        if "status" in body:
            if body["status"] not in STATUSES:
                return 400, "Statut invalide"
            s["status"] = body["status"]
            # 'next' : nouvelle consultation (élève suivant) sans quitter l'état.
            record(nid, body["status"], force=bool(body.get("next")) and body["status"] == "consult")
            if body["status"] != "pause":
                s.pop("back", None)
        if "back" in body:
            back = body["back"]
            if back and (not TIME_RE.match(str(back)) or s["status"] != "pause"):
                return 400, "Heure de retour invalide"
            if back:
                s["back"] = back
            else:
                s.pop("back", None)
        if body.get("resetHours"):
            s.pop("hours", None)
        elif "start" in body and "end" in body:
            if not (TIME_RE.match(str(body["start"])) and TIME_RE.match(str(body["end"]))):
                return 400, "Horaires invalides"
            s["hours"] = [body["start"], body["end"]]
        # Horaires modifiés : réarme la fermeture automatique. Après l'heure de fin,
        # un choix manuel est respecté (pas de remise en 'absente' immédiate).
        if body.get("resetHours") or "start" in body:
            s["closed"] = False
        elif "status" in body:
            s["closed"] = after_hours(nid)
        s["updated"] = datetime.datetime.now().strftime("%H:%M")
        save_state()
        broadcast()
    return 200, "ok"


def after_hours(nid):
    s = state["nurses"].get(nid, {})
    now = datetime.datetime.now().strftime("%H:%M")
    return now >= (s.get("hours") or default_hours(NURSES[nid], datetime.date.today()))[1]


def auto_close():
    """À l'heure de fin, passe en 'absente' (une seule fois par fin de journée)."""
    changed = False
    for nid in NURSES:
        s = state["nurses"].setdefault(nid, {"status": "absent"})
        if s.get("closed") or not after_hours(nid):
            continue
        s["closed"] = True
        if s.get("status") != "absent":
            s["status"] = "absent"
            s.pop("back", None)
            s["updated"] = datetime.datetime.now().strftime("%H:%M")
            record(nid, "absent", auto=True)
        changed = True
    if changed:
        save_state()
    return changed


def watcher():
    stop = threading.Event()
    while not stop.wait(15):
        with lock:
            heartbeat()
            if roll_day() | auto_close():
                broadcast()


# ----------------------------------------------------------------- journal (SQLite)

def init_db():
    global db
    db = sqlite3.connect(DB_FILE, check_same_thread=False)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY, nurse TEXT NOT NULL, status TEXT NOT NULL,
            start INTEGER NOT NULL, "end" INTEGER, auto INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS events_start ON events(start);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v INTEGER);""")
    if "auto" not in [c[1] for c in db.execute("PRAGMA table_info(events)")]:   # base d'une version précédente
        db.execute("ALTER TABLE events ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")
    db.commit()


def now_ts():
    return int(time.time())


def record(nid, status, ts=None, force=False, auto=False):
    """Journalise un changement d'état (ferme l'événement ouvert, en ouvre un nouveau).
    auto=True : changement automatique (fin de journée, nouveau jour, redémarrage),
    qui ne compte pas comme un usage du bouton dans les statistiques."""
    ts = ts or now_ts()
    row = db.execute('SELECT id, status FROM events WHERE nurse=? AND "end" IS NULL', (nid,)).fetchone()
    if row and row[1] == status and not force:
        return
    if row:
        db.execute('UPDATE events SET "end"=? WHERE id=?', (ts, row[0]))
    db.execute("INSERT INTO events(nurse, status, start, auto) VALUES(?,?,?,?)", (nid, status, ts, int(auto)))
    db.commit()


def heartbeat():
    db.execute("INSERT OR REPLACE INTO meta VALUES('last_seen', ?)", (now_ts(),))
    db.commit()


def startup_reconcile():
    """Après un arrêt de plus de 2 minutes, ferme les événements restés ouverts à la
    dernière heure connue (évite de gonfler les durées), puis resynchronise."""
    ts = now_ts()
    row = db.execute("SELECT v FROM meta WHERE k='last_seen'").fetchone()
    last = row[0] if row else ts
    if ts - last > 120:
        db.execute('UPDATE events SET "end"=? WHERE "end" IS NULL', (last,))
        db.commit()
    for nid in NURSES:
        record(nid, state["nurses"].get(nid, {}).get("status", "absent"), ts, auto=True)
    heartbeat()


# --------------------------------------------------------------------- statistiques

def parse_period(query):
    today = datetime.date.today()
    try:
        d_to = datetime.date.fromisoformat(query.get("to", [""])[0]) if query.get("to") else today
        d_from = (datetime.date.fromisoformat(query["from"][0]) if query.get("from")
                  else d_to - datetime.timedelta(days=6))
    except ValueError:
        return None
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    if (d_to - d_from).days > MAX_RANGE_DAYS:
        d_from = d_to - datetime.timedelta(days=MAX_RANGE_DAYS)
    return d_from, d_to


def _ts(d):
    return int(datetime.datetime.combine(d, datetime.time.min).timestamp())


def fetch_events(d_from, d_to):
    t0, t1, now = _ts(d_from), _ts(d_to + datetime.timedelta(days=1)), now_ts()
    rows = db.execute(
        'SELECT nurse, status, start, COALESCE("end", ?), auto FROM events '
        'WHERE start < ? AND COALESCE("end", ?) > ? ORDER BY start', (now, t1, now, t0)).fetchall()
    return t0, t1, [r for r in rows if r[0] in NURSES and r[3] - r[2] >= MIN_EVENT_SECONDS]


def compute_stats(d_from, d_to):
    t0, t1, rows = fetch_events(d_from, d_to)
    days = [d_from + datetime.timedelta(days=i) for i in range((d_to - d_from).days + 1)]
    per = {nid: {"id": nid, "name": n["name"],
                 "states": {s: {"count": 0, "sec": 0} for s in STATUSES},
                 "daily": {d.isoformat(): {"count": 0, "sec": 0} for d in days},
                 "hourly": [0] * 24, "consultDurations": []} for nid, n in NURSES.items()}
    for nid, status, start, end, auto in rows:
        p = per[nid]
        sec = min(end, t1) - max(start, t0)
        p["states"][status]["sec"] += sec
        if start < t0:
            continue                                  # commencé avant la période : temps seul
        if not auto:                                  # usage réel du bouton (hors changements automatiques)
            p["states"][status]["count"] += 1
        if status == "consult":
            day = datetime.datetime.fromtimestamp(start)
            p["consultDurations"].append(end - start)
            if day.date().isoformat() in p["daily"]:
                p["daily"][day.date().isoformat()]["count"] += 1
                p["daily"][day.date().isoformat()]["sec"] += sec
            p["hourly"][day.hour] += 1
    out, tot_n, tot_sec, tot_att = [], 0, 0, 0
    for p in per.values():
        durs = p.pop("consultDurations")
        st = p["states"]
        attendance = st["present"]["sec"] + st["consult"]["sec"] + st["pause"]["sec"]
        p.update(consultations=len(durs),
                 avgConsultSec=round(sum(durs) / len(durs)) if durs else None,
                 consultSec=st["consult"]["sec"], attendanceSec=attendance,
                 occupancy=round(st["consult"]["sec"] / attendance, 4) if attendance else None,
                 daily=[{"date": k, **v} for k, v in p["daily"].items()])
        tot_n += p["consultations"]; tot_sec += sum(durs); tot_att += attendance
        out.append(p)
    return {"from": d_from.isoformat(), "to": d_to.isoformat(), "nurses": out,
            "total": {"consultations": tot_n,
                      "avgConsultSec": round(tot_sec / tot_n) if tot_n else None,
                      "occupancy": round(sum(p["consultSec"] for p in out) / tot_att, 4) if tot_att else None},
            "minEventSeconds": MIN_EVENT_SECONDS}


STATUS_FR = {"present": "Présente", "consult": "En consultation",
             "pause": "Absente temporairement", "absent": "Absente"}


# Le temps « absente » inclut les nuits et week-ends : il n'est pas présenté.
TIMED_STATUSES = ("present", "consult", "pause")


def _num(x):
    return str(x).replace(".", ",")


def stats_csv(stats):
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Infirmière", "Période", "Élèves reçus", "Durée moyenne de consultation (min)",
                "Temps en consultation (min)", "Temps de présence (min)", "Taux d'occupation (%)"]
               + [f"{STATUS_FR[s]} : nombre de fois" for s in STATUSES]
               + [f"{STATUS_FR[s]} : temps (min)" for s in TIMED_STATUSES])
    for p in stats["nurses"]:
        w.writerow([p["name"], f'{stats["from"]} au {stats["to"]}', p["consultations"],
                    _num(round(p["avgConsultSec"] / 60, 1)) if p["avgConsultSec"] else "",
                    _num(round(p["consultSec"] / 60, 1)), _num(round(p["attendanceSec"] / 60, 1)),
                    _num(round(p["occupancy"] * 100, 1)) if p["occupancy"] is not None else ""]
                   + [p["states"][s]["count"] for s in STATUSES]
                   + [_num(round(p["states"][s]["sec"] / 60, 1)) for s in TIMED_STATUSES])
    return out.getvalue()


def events_csv(d_from, d_to):
    _, _, rows = fetch_events(d_from, d_to)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Infirmière", "État", "Début", "Fin", "Durée (min)", "Origine"])
    fmt = "%Y-%m-%d %H:%M:%S"
    for nid, status, start, end, auto in rows:
        w.writerow([NURSES[nid]["name"], STATUS_FR[status],
                    datetime.datetime.fromtimestamp(start).strftime(fmt),
                    datetime.datetime.fromtimestamp(end).strftime(fmt),
                    _num(round((end - start) / 60, 1)), "Automatique" if auto else "Manuelle"])
    return out.getvalue()


# ------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Cabinet/2.0"

    def log_message(self, *args):
        pass

    def send_bytes(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code, obj):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode(),
                        "application/json; charset=utf-8")

    def send_path(self, path):
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self.send_bytes(200, f.read(), ctype)

    def send_static(self, rel):
        path = os.path.normpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            return self.send_json(404, {"error": "introuvable"})
        self.send_path(path)

    def stats_request(self, parsed, kind):
        if not check_pin(self.headers.get("X-Pin")):
            return self.send_json(403, {"error": "Code incorrect"})
        period = parse_period(urllib.parse.parse_qs(parsed.query))
        if not period:
            return self.send_json(400, {"error": "Dates invalides (AAAA-MM-JJ)"})
        with lock:
            if kind == "json":
                return self.send_json(200, compute_stats(*period))
            body = (stats_csv(compute_stats(*period)) if kind == "summary"
                    else events_csv(*period))
        name = f"{'statistiques' if kind == 'summary' else 'evenements'}_{period[0]}_{period[1]}.csv"
        self.send_bytes(200, ("﻿" + body).encode("utf-8"), "text/csv; charset=utf-8",
                        {"Content-Disposition": f'attachment; filename="{name}"'})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.send_static("index.html")
        elif path == "/admin":
            self.send_static("admin.html")
        elif path == "/stats":
            self.send_static("stats.html")
        elif path == "/logo":
            self.send_path(logo_path())
        elif path == "/api/state":
            with lock:
                roll_day()
                self.send_json(200, snapshot())
        elif path == "/api/events":
            self.stream_events()
        elif path == "/api/stats":
            self.stats_request(parsed, "json")
        elif path == "/api/stats.csv":
            self.stats_request(parsed, "summary")
        elif path == "/api/events.csv":
            self.stats_request(parsed, "events")
        elif path.startswith("/static/"):
            self.send_static(path[len("/static/"):])
        else:
            self.send_json(404, {"error": "introuvable"})

    def do_POST(self):
        if self.path != "/api/set":
            return self.send_json(404, {"error": "introuvable"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(min(length, 4096)) or b"{}")
        except ValueError:
            return self.send_json(400, {"error": "JSON invalide"})
        code, msg = apply_change(body)
        self.send_json(code, {"ok": code == 200, "error": None if code == 200 else msg})

    def stream_events(self):
        q = queue.Queue()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        with lock:
            roll_day()
            q.put(json.dumps(snapshot(), ensure_ascii=False))
            subscribers.add(q)
        try:
            while True:
                try:
                    self.wfile.write(f"data: {q.get(timeout=15)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            subscribers.discard(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    init_db()
    load_state()
    startup_reconcile()
    roll_day()
    threading.Thread(target=watcher, daemon=True).start()
    port = int(os.environ.get("PORT", CONFIG.get("port", 8080)))
    print(f"Cabinet en écoute sur le port {port}", flush=True)
    Server(("0.0.0.0", port), Handler).serve_forever()
