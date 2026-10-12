"""Authentification : comptes (infirmières, administration), codes hachés, sessions,
limitation des tentatives. Bibliothèque standard uniquement.

Comptes
  nurse:<id>   une infirmière : pilote SON panneau, voit SES statistiques
  admin:<id>   l'administration : voit les statistiques de toutes, lecture seule

Un code est stocké haché (scrypt) dans config.json (champ "pinHash", écrit par
deploy/pins.py). Un "pin" en clair est toléré dans le fichier (il est haché en mémoire).
Un compte sans code ne peut pas se connecter.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)
MIN_PIN_LENGTH = 6

# Limitation des tentatives (valeurs par défaut)
IP_MAX_FAILS = 5            # échecs avant blocage de l'adresse
IP_LOCK_BASE = 300          # 5 min, doublé à chaque blocage, plafonné
IP_LOCK_MAX = 3600
ACCOUNT_MAX_FAILS = 20      # échecs (toutes adresses) sur la fenêtre avant blocage du compte
ACCOUNT_WINDOW = 900
ACCOUNT_LOCK = 900
SESSION_MAX_AGE = 24 * 3600  # durée absolue maximale d'une session


def hash_pin(pin, salt=None):
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(pin.encode("utf-8"), salt=salt, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_pin(pin, stored):
    """Comparaison à temps constant ; stored = 'scrypt$sel$hash'."""
    try:
        _, salt, digest = stored.split("$")
        expected = bytes.fromhex(digest)
        got = hashlib.scrypt(pin.encode("utf-8"), salt=bytes.fromhex(salt), **SCRYPT)
        return hmac.compare_digest(got, expected)
    except (ValueError, AttributeError):
        return False


_DUMMY = hash_pin("x" * MIN_PIN_LENGTH)   # pour garder un temps de réponse constant


class Auth:
    def __init__(self, config_path, log=print):
        self.config_path = config_path
        self.log = log
        self.lock = threading.Lock()
        self.sessions = {}            # jeton -> {role, id, name, expires, created}
        self.ip_fails = {}            # ip -> {fails, level, until}
        self.acct_fails = {}          # compte -> [horodatages d'échecs]
        self.acct_until = {}          # compte -> fin de blocage
        self.accounts = {}
        self.cfg = {}
        self._mtime = 0
        self.reload(force=True)

    # ------------------------------------------------------------ configuration
    def reload(self, force=False):
        """Relit config.json si le fichier a changé : un nouveau code est pris en compte
        sans redémarrer ; les sessions d'un compte dont le code a changé sont fermées."""
        try:
            mtime = os.path.getmtime(self.config_path)
        except OSError:
            return
        if not force and mtime == self._mtime:
            return
        with open(self.config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        accounts = {}
        for role, key in (("nurse", "nurses"), ("admin", "admins")):
            for a in cfg.get(key, []):
                stored = a.get("pinHash") or (hash_pin(str(a["pin"])) if a.get("pin") else None)
                accounts[f"{role}:{a['id']}"] = {"role": role, "id": a["id"], "name": a.get("name", a["id"]),
                                                 "hash": stored, "plain": bool(a.get("pin") and not a.get("pinHash"))}
        with self.lock:
            for k, acc in accounts.items():
                old = self.accounts.get(k)
                if old and old["hash"] != acc["hash"]:
                    self._drop_sessions(k)
            for k in set(self.accounts) - set(accounts):
                self._drop_sessions(k)
            self.accounts, self.cfg, self._mtime = accounts, cfg, mtime

    def _drop_sessions(self, key):
        role, ident = key.split(":", 1)
        for t in [t for t, s in self.sessions.items() if s["role"] == role and s["id"] == ident]:
            del self.sessions[t]

    def ttl(self, role):
        hours = (self.cfg.get("sessionHours") or {})
        return int(float(hours.get(role, 12 if role == "nurse" else 2)) * 3600)

    def public_accounts(self):
        """Noms affichables sur l'écran de connexion (jamais les codes)."""
        return {"nurses": [{"id": a["id"], "name": a["name"]} for a in self.accounts.values() if a["role"] == "nurse"],
                "admins": [{"id": a["id"], "name": a["name"]} for a in self.accounts.values() if a["role"] == "admin"]}

    def warnings(self):
        out = []
        for k, a in self.accounts.items():
            if not a["hash"]:
                out.append(f"{k} : aucun code défini (connexion impossible) - voir deploy/pins.py")
            elif a["plain"]:
                out.append(f"{k} : code en clair dans config.json - utilisez deploy/pins.py (code haché)")
        if not any(a["role"] == "admin" for a in self.accounts.values()):
            out.append("aucun compte administration : les statistiques ne seront accessibles qu'aux infirmières (les leurs)")
        return out

    # ------------------------------------------------------------ connexion
    def _locked(self, ip, key, now):
        e = self.ip_fails.get(ip)
        if e and e["until"] > now:
            return int(e["until"] - now) + 1
        until = self.acct_until.get(key, 0)
        if until > now:
            return int(until - now) + 1
        return 0

    def _fail(self, ip, key, now):
        e = self.ip_fails.setdefault(ip, {"fails": 0, "level": 0, "until": 0})
        e["fails"] += 1
        if e["fails"] >= IP_MAX_FAILS:
            e["fails"], e["level"] = 0, e["level"] + 1
            e["until"] = now + min(IP_LOCK_BASE * 2 ** (e["level"] - 1), IP_LOCK_MAX)
            self.log(f"[auth] adresse {ip} bloquée {int(e['until'] - now)} s (échecs répétés)")
        hist = [t for t in self.acct_fails.get(key, []) if t > now - ACCOUNT_WINDOW] + [now]
        self.acct_fails[key] = hist
        if len(hist) >= ACCOUNT_MAX_FAILS:
            self.acct_until[key] = now + ACCOUNT_LOCK
            self.acct_fails[key] = []
            self.log(f"[auth] compte {key} verrouillé {ACCOUNT_LOCK} s (trop d'échecs, toutes adresses)")

    def login(self, role, ident, pin, ip):
        """Retourne (ok, data) ; data = infos de session ou {'error', 'retry'}."""
        self.reload()
        key = f"{role}:{ident}"
        now = time.time()
        with self.lock:
            wait = self._locked(ip, key, now)
        if wait:
            return False, {"error": "Trop de tentatives. Réessayez dans %d min %02d s." % (wait // 60, wait % 60), "retry": wait}
        acc = self.accounts.get(key)
        pin = str(pin or "")[:128]
        # le hachage est toujours calculé (temps constant, pas d'indice sur l'existence du compte)
        good = verify_pin(pin, acc["hash"] if acc and acc["hash"] else _DUMMY)
        with self.lock:
            if not (acc and acc["hash"] and good):
                self._fail(ip, key, now)
                self.log(f"[auth] échec de connexion {key} depuis {ip}")
                return False, {"error": "Code incorrect." if acc and acc["hash"] else "Ce compte n'est pas activé : aucun code défini."}
            self.ip_fails.pop(ip, None)
            self.acct_fails.pop(key, None)
            token = secrets.token_urlsafe(32)
            ttl = self.ttl(role)
            self.sessions[token] = {"role": role, "id": ident, "name": acc["name"], "created": now,
                                    "expires": now + ttl}
            self.log(f"[auth] connexion {key} depuis {ip}")
            return True, {"token": token, "ttl": ttl, "role": role, "id": ident, "name": acc["name"]}

    def session(self, token):
        """Session valide (et prolongée) ou None."""
        if not token:
            return None
        self.reload()                 # un code changé ferme tout de suite les sessions du compte
        now = time.time()
        with self.lock:
            s = self.sessions.get(token)
            if not s:
                return None
            if s["expires"] < now or s["created"] + SESSION_MAX_AGE < now:
                del self.sessions[token]
                return None
            s["expires"] = now + self.ttl(s["role"])        # session glissante
            return dict(s)

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def purge(self):
        now = time.time()
        with self.lock:
            for t in [t for t, s in self.sessions.items() if s["expires"] < now]:
                del self.sessions[t]
            for ip in [ip for ip, e in self.ip_fails.items() if e["until"] < now - 3600 and e["fails"] == 0]:
                del self.ip_fails[ip]
