#!/usr/bin/env python3
"""Affichage de présence des cabinets d'infirmières.

Serveur sans dépendance (bibliothèque standard uniquement).
  /            page d'affichage (écran du couloir, mode kiosque)
  /admin       page de commande des infirmières (téléphone / PC)
  /stats       statistiques d'activité (consultations, occupation...)
  /logo        logo de l'établissement (défini dans config.json)
  /api/state   état courant (JSON, public : c'est ce que l'écran affiche)
  /api/events  flux temps réel (Server-Sent Events, public)
  /api/accounts, /api/login, /api/logout, /api/me   connexion (codes personnels)
  /api/set     modification d'un état : l'infirmière connectée, pour elle seule
  /api/stats, /api/stats.csv, /api/events.csv   statistiques : connexion obligatoire
                (administration : toutes ; infirmière : les siennes)
Avec "tls" dans config.json, les pages protégées et les API de connexion ne sont
servies qu'en HTTPS : les codes ne circulent jamais en clair.
"""
import csv
import datetime
import hashlib
import io
import ipaddress
import json
import mimetypes
import os
import queue
import re
import sqlite3
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from auth import Auth
from mdns import Responder

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
STATE_FILE = os.path.join(BASE, "state.json")
DB_FILE = os.path.join(BASE, "data.db")
DEFAULT_LOGO = os.path.join(STATIC, "logo-default.svg")
STATUSES = ("present", "absent", "consult", "pause")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_RANGE_DAYS = 800

CONFIG_PATH = os.path.join(BASE, "config.json")
with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = json.load(f)
NURSES = {n["id"]: n for n in CONFIG["nurses"]}
MIN_EVENT_SECONDS = int(CONFIG.get("minEventSeconds", 10))
AUTH_LOG = os.path.join(BASE, "auth.log")


def auth_log(msg):
    """Journal des connexions : écran du service + fichier auth.log (conservé, 1 Mo puis rotation)."""
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        if os.path.exists(AUTH_LOG) and os.path.getsize(AUTH_LOG) > 1_000_000:
            os.replace(AUTH_LOG, AUTH_LOG + ".1")
        fd = os.open(AUTH_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)   # lisible par ce compte seulement
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


AUTH = Auth(CONFIG_PATH, log=auth_log)
TLS = CONFIG.get("tls") or {}          # {"port": 8443, "cert": "certs/server.crt", "key": "certs/server.key"}
COOKIE = "cabinet_session"

# Sous-domaines : avec "domain": "pi-infirmerie.local", le panneau des infirmières est servi sur
# admin.pi-infirmerie.local et les statistiques sur stats.pi-infirmerie.local (à la racine "/"),
# l'écran public sur pi-infirmerie.local. "hosts" permet de choisir d'autres noms. Un accès par
# l'adresse IP (ou un autre nom) garde les chemins /admin et /stats.
# Noms par défaut : sous-domaines classiques (admin.<domaine>, stats.<domaine>) ; pour un nom
# « .local » à un seul niveau (annoncé en mDNS, sans DNS), des noms eux aussi à un seul niveau,
# car Windows ne résout en mDNS que ceux-là : <nom>-admin.local et <nom>-stats.local.
DOMAIN = (CONFIG.get("domain") or "").lower().strip(".")
HOSTS = {}
if DOMAIN:
    if DOMAIN.endswith(".local") and DOMAIN.count(".") == 1:
        HOSTS = {"admin": f"{DOMAIN[:-6]}-admin.local", "stats": f"{DOMAIN[:-6]}-stats.local"}
    else:
        HOSTS = {"admin": f"admin.{DOMAIN}", "stats": f"stats.{DOMAIN}"}
    HOSTS.update({k: str(v).lower() for k, v in (CONFIG.get("hosts") or {}).items() if k in HOSTS})

# Réseau interne / externe. Une requête dont l'adresse source n'est pas dans "internalNetworks" est
# « externe » (Internet) : second facteur exigé, aucune donnée lisible sans connexion, liste des
# comptes masquée, limites de connexions plus strictes. Le pare-feu doit conserver l'adresse source
# réelle des clients (pas de NAT source), sinon tout paraîtrait venir du pare-feu.
INTERNAL_NETS = [ipaddress.ip_network(n, strict=False) for n in CONFIG.get(
    "internalNetworks", ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fe80::/10"])]
PUBLIC_DISPLAY = CONFIG.get("publicDisplay", "internal")    # "internal" : l'écran public est lisible depuis le réseau interne ; "local" : seulement depuis le Pi (kiosque) ; "everyone" : aussi depuis Internet
LIMITS = {"total": 400, "perIpInternal": 100, "perIpExternal": 20, "sseTotal": 100, "sseExternal": 3, "sseInternal": 20}
LIMITS.update(CONFIG.get("limits") or {})


def default_gateway():
    """Adresse IPv4 de la passerelle par défaut du Pi (lue dans /proc/net/route), ou None."""
    try:
        with open("/proc/net/route", encoding="ascii") as f:
            next(f)
            for line in f:
                cols = line.split()
                if cols[1] == "00000000" and int(cols[3], 16) & 2:
                    return socket.inet_ntoa(struct.pack("<L", int(cols[2], 16)))
    except (OSError, ValueError, IndexError, StopIteration):
        pass
    return None


# Toujours « externes », même si l'adresse est privée : le trafic qui arrive AVEC l'adresse de la
# passerelle est du trafic traduit par le pare-feu (NAT source), donc d'origine inconnue, peut-être
# Internet. Sans cette règle, une erreur de NAT ferait passer Internet pour le réseau interne.
EXTERNAL_NETS = [ipaddress.ip_network(n, strict=False) for n in CONFIG.get("externalAddresses", [])]
_gw = default_gateway() if CONFIG.get("gatewayIsExternal", True) else None
if _gw:
    EXTERNAL_NETS.append(ipaddress.ip_network(_gw + "/32"))


def is_internal(ip):
    """Vrai si l'adresse appartient au réseau interne (ou à la machine elle-même)."""
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    if any(addr in net for net in EXTERNAL_NETS):
        return False
    return any(addr in net for net in INTERNAL_NETS)


def is_loopback(ip):
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_loopback
    except ValueError:
        return False


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
            "phone": str(nurse.get("phone", ""))[:12],
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
            "urls": site_urls(),
            "nurses": out}


def site_url(host):
    """Adresse complète d'un site : HTTPS sur le port TLS si le HTTPS est actif, sinon HTTP."""
    if TLS_ACTIVE:
        scheme, port = "https", int(TLS.get("port", 8443))
    else:
        scheme, port = "http", int(os.environ.get("PORT", CONFIG.get("port", 8080)))
    return f"{scheme}://{host}{'' if (scheme, port) in (('https', 443), ('http', 80)) else ':%d' % port}/"


def site_urls():
    """Adresses des trois sites (vide si aucun sous-domaine n'est configuré)."""
    if not DOMAIN:
        return {}
    return {"public": site_url(DOMAIN), "admin": site_url(HOSTS["admin"]), "stats": site_url(HOSTS["stats"])}


def broadcast():
    data = json.dumps(snapshot(), ensure_ascii=False)
    for q in list(subscribers):
        q.put(data)


def apply_change(body, nid):
    """Applique une modification pour l'infirmière nid (déjà authentifiée et autorisée
    par le gestionnaire HTTP). Retourne (code HTTP, message)."""
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
        AUTH.purge()                  # sessions expirées, blocages anciens
        reload_cert_if_changed()      # certificat renouvelé
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


def fetch_events(d_from, d_to, nurse=None):
    """Événements de la période ; nurse = identifiant pour n'en garder qu'une."""
    t0, t1, now = _ts(d_from), _ts(d_to + datetime.timedelta(days=1)), now_ts()
    rows = db.execute(
        'SELECT nurse, status, start, COALESCE("end", ?), auto FROM events '
        'WHERE start < ? AND COALESCE("end", ?) > ? ORDER BY start', (now, t1, now, t0)).fetchall()
    return t0, t1, [r for r in rows if r[0] in NURSES and nurse in (None, r[0])
                    and r[3] - r[2] >= MIN_EVENT_SECONDS]


def compute_stats(d_from, d_to, nurse=None):
    t0, t1, rows = fetch_events(d_from, d_to, nurse)
    days = [d_from + datetime.timedelta(days=i) for i in range((d_to - d_from).days + 1)]
    per = {nid: {"id": nid, "name": n["name"],
                 "states": {s: {"count": 0, "sec": 0} for s in STATUSES},
                 "daily": {d.isoformat(): {"count": 0, "sec": 0} for d in days},
                 "hourly": [0] * 24, "consultDurations": []}
           for nid, n in NURSES.items() if nurse in (None, nid)}
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


def events_csv(d_from, d_to, nurse=None):
    _, _, rows = fetch_events(d_from, d_to, nurse)
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

TLS_ACTIVE = False            # vrai si le serveur HTTPS a pu démarrer (certificat présent)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                               "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
}
HSTS = CONFIG.get("hsts", True)       # force le HTTPS dans le navigateur pendant 1 an après la première visite
MAX_BODY = 4096


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30                      # coupe les connexions inactives (clients lents ou malveillants)

    def log_message(self, *args):
        pass

    def version_string(self):
        return "cabinet"              # ne révèle ni la version de Python ni celle du serveur

    # ------------------------------------------------------------- utilitaires
    @property
    def is_tls(self):
        return bool(getattr(self.server, "is_tls", False))

    @property
    def external(self):
        """Vrai si la requête vient de l'extérieur du réseau interne (Internet)."""
        return not is_internal(self.client_address[0])

    def send_bytes(self, code, body, ctype, extra=None, cache="no-cache"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        if HSTS and self.is_tls:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code, obj, extra=None):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode(),
                        "application/json; charset=utf-8", extra, cache="no-store")

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

    def require_tls(self, page=False):
        """Vrai si la requête peut continuer. HTTPS actif mais requête en clair :
        redirection (pages) ou refus (API), pour que les codes ne circulent jamais en clair."""
        if not TLS_ACTIVE or self.is_tls:
            return True
        if page:
            host = (self.headers.get("Host") or "localhost").split(":")[0]
            port = int(TLS.get("port", 8443))
            target = f"https://{host}{'' if port == 443 else ':%d' % port}{self.path}"
            self.send_bytes(302, b"", "text/plain", {"Location": target})
        else:
            self.send_json(403, {"error": "Connexion HTTPS requise."})
        return False

    def token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def session(self):
        return AUTH.session(self.token())

    def host_name(self):
        return (self.headers.get("Host") or "").split(":")[0].lower()

    def host_role(self):
        """'public', 'admin' ou 'stats' si le nom utilisé est l'un des noms configurés, sinon None
        (adresse IP, autre nom : les chemins /admin et /stats fonctionnent comme avant)."""
        host = self.host_name()
        if not DOMAIN:
            return None
        if host == DOMAIN:
            return "public"
        return next((role for role, name in HOSTS.items() if host == name), None)

    def cookie(self, value, max_age):
        attrs = f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
        host = self.host_name()
        if DOMAIN and (host == DOMAIN or host.endswith("." + DOMAIN)):
            attrs += f"; Domain={DOMAIN}"            # la même connexion sert pour admin.* et stats.*
        return attrs + ("; Secure" if self.is_tls else "")

    def serve_page(self, page):
        """Page 'admin' ou 'stats' : au bon nom d'hôte (sinon redirection), et en HTTPS."""
        role = self.host_role()
        if role is not None and role != page:
            return self.send_bytes(302, b"", "text/plain", {"Location": site_url(HOSTS[page])})
        if self.require_tls(page=True):
            self.send_static(page + ".html")

    def same_origin(self):
        """Protection CSRF : une requête POST de navigateur porte un en-tête Origin."""
        origin = self.headers.get("Origin")
        return not origin or urllib.parse.urlparse(origin).netloc == self.headers.get("Host", "")

    def read_body(self):
        """Lit TOUJOURS le corps de la requête, même si elle est ensuite refusée : des octets non
        lus sur une connexion persistante seraient pris pour le début de la requête suivante.
        Retourne les octets, ou None si le corps est invalide ou trop gros (connexion fermée)."""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            return None
        return self.rfile.read(length) if length else b""

    def parse_json(self, raw):
        if raw is None or not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return None
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------- statistiques
    def stats_request(self, parsed, kind):
        sess = self.session()
        if not sess:
            return self.send_json(401, {"error": "Connexion requise."})
        query = urllib.parse.parse_qs(parsed.query)
        period = parse_period(query)
        if not period:
            return self.send_json(400, {"error": "Dates invalides (AAAA-MM-JJ)"})
        nurse = query.get("nurse", [""])[0] or None        # filtre : une seule infirmière
        if nurse is not None and nurse not in NURSES:
            return self.send_json(400, {"error": "Infirmière inconnue"})
        if sess["role"] == "nurse":                         # une infirmière ne voit que ses statistiques
            if nurse not in (None, sess["id"]):
                return self.send_json(403, {"error": "Vous ne pouvez consulter que vos propres statistiques."})
            nurse = sess["id"]
        with lock:
            if kind == "json":
                return self.send_json(200, compute_stats(*period, nurse))
            body = (stats_csv(compute_stats(*period, nurse)) if kind == "summary"
                    else events_csv(*period, nurse))
        name = (f"{'statistiques' if kind == 'summary' else 'evenements'}"
                f"{'_' + nurse if nurse else ''}_{period[0]}_{period[1]}.csv")
        self.send_bytes(200, ("﻿" + body).encode("utf-8"), "text/csv; charset=utf-8",
                        {"Content-Disposition": f'attachment; filename="{name}"'}, cache="no-store")

    def may_read_state(self):
        """Écran public (état courant, flux temps réel). Le Pi lui-même (kiosque) : toujours. Le réseau
        interne : si "publicDisplay" vaut "internal" (défaut). Depuis l'extérieur : jamais sans connexion."""
        ip = self.client_address[0]
        if is_loopback(ip) or PUBLIC_DISPLAY == "everyone" or (PUBLIC_DISPLAY == "internal" and is_internal(ip)):
            return True
        return self.session() is not None

    def hidden_from_outside(self):
        """Aide à l'installation du certificat : réservée au réseau interne (inutile et superflue sur Internet)."""
        if self.external:
            self.send_json(404, {"error": "introuvable"})
            return True
        return False

    # ------------------------------------------------------------- GET
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            role = self.host_role()
            if role in ("admin", "stats"):               # sous-domaine : le site est à la racine
                self.serve_page(role)
            else:                                         # page principale pour tout le monde ; sans droit de lecture
                self.send_static("index.html")            # (Internet), elle n'affiche que les liens /admin et /stats
        elif path in ("/admin", "/stats"):
            self.serve_page(path[1:])
        elif path == "/certificat":                      # aide à l'installation de l'autorité locale
            if not self.hidden_from_outside():
                self.send_static("certificat.html")
        elif path == "/api/ca":
            if self.hidden_from_outside():
                return
            ca = os.path.join(BASE, "certs", "ca.crt")
            if not os.path.isfile(ca):
                return self.send_json(404, {"error": "introuvable"})
            der = ssl.PEM_cert_to_DER_cert(open(ca, encoding="ascii").read())
            fp = hashlib.sha256(der).hexdigest().upper()
            self.send_json(200, {"fingerprint": ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))})
        elif path == "/logo":
            self.send_path(logo_path())
        elif path == "/ca.crt":        # autorité de certification locale, à installer sur les appareils
            if self.hidden_from_outside():
                return
            ca = os.path.join(BASE, "certs", "ca.crt")
            self.send_path(ca) if os.path.isfile(ca) else self.send_json(404, {"error": "introuvable"})
        elif path == "/api/state":
            if not self.may_read_state():
                return self.send_json(401, {"error": "Connexion requise."})
            with lock:
                roll_day()
                self.send_json(200, snapshot())
        elif path == "/api/events":
            if not self.may_read_state():
                return self.send_json(401, {"error": "Connexion requise."})
            self.stream_events()
        elif path == "/api/accounts":
            self.send_json(200, AUTH.public_accounts(self.external))
        elif path == "/api/me":
            if not self.require_tls():
                return
            sess = self.session()
            if not sess:
                return self.send_json(401, {"ok": False})
            self.send_json(200, {"ok": True, "role": sess["role"], "id": sess["id"], "name": sess["name"],
                                 "insecure": not self.is_tls, "external": sess["external"]})
        elif path in ("/api/stats", "/api/stats.csv", "/api/events.csv"):
            if self.require_tls():
                self.stats_request(parsed, {"/api/stats": "json", "/api/stats.csv": "summary",
                                            "/api/events.csv": "events"}[path])
        elif path.startswith("/static/"):
            self.send_static(path[len("/static/"):])
        else:
            self.send_json(404, {"error": "introuvable"})

    # ------------------------------------------------------------- POST
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        raw = self.read_body()                       # avant tout refus (voir read_body)
        if path not in ("/api/login", "/api/logout", "/api/set"):
            return self.send_json(404, {"error": "introuvable"})
        if not self.require_tls():
            return
        if not self.same_origin():
            return self.send_json(403, {"error": "Origine refusée."})
        if path == "/api/logout":
            AUTH.logout(self.token())
            return self.send_json(200, {"ok": True}, {"Set-Cookie": self.cookie("", 0)})
        body = self.parse_json(raw)
        if body is None:
            return self.send_json(400, {"error": "Requête invalide."})

        if path == "/api/login":
            role, ident = body.get("role"), body.get("id")
            if role not in ("nurse", "admin") or not isinstance(ident, str):
                return self.send_json(400, {"error": "Requête invalide."})
            ok, data = AUTH.login(role, ident, body.get("pin"), self.client_address[0],
                                 external=self.external, code=body.get("code"))
            if not ok:
                return self.send_json(429 if data.get("retry") else 401, {"ok": False, **data})
            self.send_json(200, {"ok": True, "role": data["role"], "id": data["id"], "name": data["name"]},
                           {"Set-Cookie": self.cookie(data["token"], data["ttl"])})
            return

        # /api/set : l'infirmière connectée modifie SON panneau, rien d'autre
        sess = self.session()
        if not sess:
            return self.send_json(401, {"ok": False, "error": "Connexion requise."})
        if sess["role"] != "nurse":
            return self.send_json(403, {"ok": False, "error": "Ce compte est en lecture seule."})
        if body.get("id") not in (None, sess["id"]):
            return self.send_json(403, {"ok": False, "error": "Vous ne pouvez modifier que votre propre panneau."})
        code, msg = apply_change(body, sess["id"])
        self.send_json(code, {"ok": code == 200, "error": None if code == 200 else msg})

    def stream_events(self):
        ip = self.client_address[0]
        cap = LIMITS["sseInternal"] if is_internal(ip) else LIMITS["sseExternal"]
        with SSE_LOCK:                   # un flux ouvert occupe un fil : on en limite le nombre (total et par adresse)
            if SSE["total"] >= LIMITS["sseTotal"] or SSE["ip"].get(ip, 0) >= cap:
                return self.send_json(429, {"error": "Trop de flux ouverts."})
            SSE["total"] += 1
            SSE["ip"][ip] = SSE["ip"].get(ip, 0) + 1
        q = queue.Queue()
        subscribed = False
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            with lock:
                roll_day()
                q.put(json.dumps(snapshot(), ensure_ascii=False))
                subscribers.add(q)
                subscribed = True
            while True:
                try:
                    self.wfile.write(f"data: {q.get(timeout=15)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            if subscribed:
                subscribers.discard(q)
            with SSE_LOCK:
                SSE["total"] -= 1
                SSE["ip"][ip] = SSE["ip"].get(ip, 1) - 1
                if SSE["ip"][ip] <= 0:
                    SSE["ip"].pop(ip, None)


SSE = {"total": 0, "ip": {}}
SSE_LOCK = threading.Lock()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64
    is_tls = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.conn_lock = threading.Lock()
        self.conns = {}               # adresse -> connexions ouvertes
        self.conn_total = 0

    def process_request(self, request, client_address):
        """Limite le nombre de connexions simultanées (total et par adresse) : un client qui en ouvre
        des milliers ne peut pas saturer le Pi. Au-delà, la connexion est fermée tout de suite."""
        ip = client_address[0]
        cap = LIMITS["perIpInternal"] if is_internal(ip) else LIMITS["perIpExternal"]
        with self.conn_lock:
            if self.conn_total >= LIMITS["total"] or self.conns.get(ip, 0) >= cap:
                refused = True
            else:
                refused = False
                self.conns[ip] = self.conns.get(ip, 0) + 1
                self.conn_total += 1
        if refused:
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            ip = client_address[0]
            with self.conn_lock:
                self.conn_total -= 1
                self.conns[ip] = self.conns.get(ip, 1) - 1
                if self.conns[ip] <= 0:
                    self.conns.pop(ip, None)

    def handle_error(self, request, client_address):
        pass          # déconnexions brusques, certificat refusé par un client... : sans importance


TLS_CTX = None                # contexte TLS du serveur HTTPS (rechargé quand le certificat change)
TLS_FILES = ("", "")
TLS_MTIME = 0


def tls_mtime():
    try:
        return max(os.path.getmtime(TLS_FILES[0]), os.path.getmtime(TLS_FILES[1]))
    except OSError:
        return 0


def reload_cert_if_changed():
    """Renouvellement du certificat (Let's Encrypt, setup-https.sh...) pris en compte sans redémarrer :
    les nouvelles connexions utilisent le nouveau certificat."""
    global TLS_MTIME
    if TLS_CTX is None:
        return
    m = tls_mtime()
    if m and m != TLS_MTIME:
        try:
            TLS_CTX.load_cert_chain(*TLS_FILES)
            TLS_MTIME = m
            print("[sécurité] certificat HTTPS rechargé", flush=True)
        except (ssl.SSLError, OSError) as e:
            print(f"[sécurité] certificat HTTPS illisible, l'ancien est conservé ({e})", flush=True)


def start_tls():
    """Démarre le serveur HTTPS si config.json le demande et si le certificat existe."""
    global TLS_ACTIVE, TLS_CTX, TLS_FILES, TLS_MTIME
    if not TLS:
        return
    cert, key = (os.path.join(BASE, TLS.get("cert", "certs/server.crt")),
                 os.path.join(BASE, TLS.get("key", "certs/server.key")))
    if not (os.path.isfile(cert) and os.path.isfile(key)):
        print("[sécurité] certificat HTTPS introuvable : HTTPS désactivé (voir deploy/setup-https.sh)", flush=True)
        return
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    TLS_CTX, TLS_FILES = ctx, (cert, key)
    TLS_MTIME = tls_mtime()
    port = int(TLS.get("port", 8443))
    srv = Server((CONFIG.get("tlsBind", "0.0.0.0"), port), Handler)
    srv.is_tls = True
    # la poignée de main TLS se fait dans le fil de la requête : un client lent ne bloque pas l'accueil
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    TLS_ACTIVE = True
    print(f"HTTPS en écoute sur le port {port}", flush=True)


if __name__ == "__main__":
    if "--print-hosts" in sys.argv:       # utilisé par deploy/setup-https.sh : noms à mettre dans le certificat
        print(" ".join(dict.fromkeys(([DOMAIN] if DOMAIN else []) + list(HOSTS.values()))))
        sys.exit(0)
    init_db()
    load_state()
    startup_reconcile()
    roll_day()
    for w in AUTH.warnings():
        print("[sécurité]", w, flush=True)
    start_tls()
    # Noms « .local » des sous-domaines : annoncés sur le réseau local (mDNS), sans rien installer.
    # Pour un vrai nom DNS donné par le service informatique, ils sont ignorés ("mdns": false).
    mdns_names = [h for h in HOSTS.values() if h.endswith(".local")]
    if mdns_names and CONFIG.get("mdns", True) and os.environ.get("CABINET_NO_MDNS") != "1":
        # le nom de base est aussi annoncé par avahi ; on le double : plus rapide et plus fiable (le serveur
        # n'écoute qu'en IPv4, donc répondre « pas d'adresse IPv6 » évite aux clients une attente inutile)
        base = [DOMAIN] if DOMAIN.endswith(".local") and DOMAIN.count(".") == 1 else []
        Responder(mdns_names + base, log=lambda m: print(m, flush=True)).start()
    if not TLS_ACTIVE:
        print("[sécurité] HTTPS inactif : les codes de connexion circuleraient en clair sur le réseau "
              "(voir deploy/setup-https.sh)", flush=True)
    threading.Thread(target=watcher, daemon=True).start()
    port = int(os.environ.get("PORT", CONFIG.get("port", 8080)))
    print(f"Cabinet en écoute sur le port {port}", flush=True)
    # "httpBind": "127.0.0.1" : le port HTTP n'est joignable que depuis le Pi (kiosque) ; seul le port HTTPS est exposé
    Server((CONFIG.get("httpBind", "0.0.0.0"), port), Handler).serve_forever()
