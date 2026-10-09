#!/usr/bin/env python3
"""Affichage de présence des cabinets d'infirmières - Lycée Louis Rascol.

Serveur sans dépendance (bibliothèque standard uniquement).
  /            page d'affichage (écran du couloir, mode kiosque)
  /admin       page de commande des infirmières (téléphone / PC)
  /api/state   état courant (JSON)
  /api/events  flux temps réel (Server-Sent Events)
  /api/set     modification d'un état (POST JSON)
"""
import datetime
import hmac
import json
import mimetypes
import os
import queue
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
STATE_FILE = os.path.join(BASE, "state.json")
STATUSES = ("present", "absent", "consult", "pause")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

with open(os.path.join(BASE, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)
NURSES = {n["id"]: n for n in CONFIG["nurses"]}

lock = threading.Lock()
subscribers = set()
state = {"day": "", "nurses": {}}


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
    roll_day()


def roll_day():
    """Chaque nouveau jour : tout le monde repasse en 'absente', horaires par défaut."""
    today = datetime.date.today().isoformat()
    if state.get("day") == today:
        return False
    state["day"] = today
    state["nurses"] = {nid: {"status": "absent"} for nid in NURSES}
    save_state()
    return True


def default_hours(nurse, date):
    hours = nurse.get("hours", {})
    return hours.get(DAYS[date.weekday()]) or hours.get("default") or ["08:00", "17:00"]


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
    return {"title": CONFIG.get("title", ""), "pinRequired": bool(CONFIG.get("pin")),
            "nurses": out}


def broadcast():
    data = json.dumps(snapshot(), ensure_ascii=False)
    for q in list(subscribers):
        q.put(data)


def apply_change(body):
    """Retourne (code HTTP, message)."""
    pin = str(CONFIG.get("pin") or "")
    if pin and not hmac.compare_digest(pin, str(body.get("pin", ""))):
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


def after_hours(nurse_id):
    s = state["nurses"].get(nid := nurse_id, {})
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
        changed = True
    if changed:
        save_state()
    return changed


def day_watcher():
    stop = threading.Event()
    while not stop.wait(15):
        with lock:
            if roll_day() | auto_close():
                broadcast()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Cabinet/1.0"

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

    def send_file(self, rel):
        path = os.path.normpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            return self.send_json(404, {"error": "introuvable"})
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self.send_bytes(200, f.read(), ctype)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.send_file("index.html")
        elif path == "/admin":
            self.send_file("admin.html")
        elif path == "/api/state":
            with lock:
                roll_day()
                self.send_json(200, snapshot())
        elif path == "/api/events":
            self.stream_events()
        elif path.startswith("/static/"):
            self.send_file(path[len("/static/"):])
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
    load_state()
    threading.Thread(target=day_watcher, daemon=True).start()
    port = int(os.environ.get("PORT", CONFIG.get("port", 8080)))
    print(f"Cabinet en écoute sur le port {port}", flush=True)
    Server(("0.0.0.0", port), Handler).serve_forever()
