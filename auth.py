"""Authentification : comptes (infirmières, administration), codes hachés, second facteur (TOTP),
sessions, limitation des tentatives. Bibliothèque standard uniquement.

Comptes
  nurse:<id>   une infirmière : pilote SON panneau, voit SES statistiques
  admin:<id>   l'administration : voit les statistiques de toutes, lecture seule

Un code est stocké haché (scrypt) dans config.json (champ "pinHash", écrit par
deploy/pins.py). Un "pin" en clair est toléré dans le fichier (il est haché en mémoire).
Un compte sans code ne peut pas se connecter.

Accès « externes » (depuis une adresse hors du réseau interne, donc Internet) :
  - second facteur obligatoire par défaut ("totp": "external") : un compte sans second facteur
    ne peut pas se connecter depuis l'extérieur ;
  - blocages comptés à part : des échecs venus d'Internet ne verrouillent JAMAIS la connexion
    des infirmières depuis le lycée ;
  - sessions plus courtes.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import threading
import time

SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)
MIN_PIN_LENGTH = 6

# Limitation des tentatives : (échecs avant blocage, durée) selon la provenance
IP_MAX_FAILS = 5            # échecs avant blocage de l'adresse
IP_LOCK_BASE = 300          # 5 min, doublé à chaque blocage, plafonné
IP_LOCK_MAX = 3600
ACCOUNT_LIMITS = {False: (20, 900), True: (10, 900)}   # {externe: (échecs sur la fenêtre, durée de blocage)}
ACCOUNT_WINDOW = 900
SESSION_MAX_AGE = {False: 24 * 3600, True: 12 * 3600}  # durée absolue maximale (interne / externe)
MAX_HASHING = 2             # calculs de hachage simultanés (le hachage est volontairement coûteux)

TOTP_STEP, TOTP_DIGITS = 30, 6


# ---------------------------------------------------------------------------- codes
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


# --------------------------------------------------------------- second facteur (TOTP, RFC 6238)
def new_totp_secret():
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def totp_at(secret, counter):
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** TOTP_DIGITS
    return f"{code:0{TOTP_DIGITS}d}"


def totp_check(secret, code, now=None, window=1, after=None):
    """Retourne le numéro de période si le code est valable (±1 période d'horloge), sinon None.
    after : dernier numéro déjà accepté ; un code déjà utilisé (ou plus ancien) est refusé."""
    code = str(code or "").replace(" ", "")
    if not (code.isdigit() and len(code) == TOTP_DIGITS):
        return None
    counter = int((now if now is not None else time.time()) // TOTP_STEP)
    found = None
    for k in range(-window, window + 1):                # toutes les périodes sont testées (temps constant)
        if hmac.compare_digest(totp_at(secret, counter + k), code) and (after is None or counter + k > after):
            found = counter + k
    return found


def totp_uri(secret, label, issuer="Cabinet infirmieres"):
    from urllib.parse import quote
    return (f"otpauth://totp/{quote(issuer)}:{quote(label)}?secret={secret}&issuer={quote(issuer)}"
            f"&digits={TOTP_DIGITS}&period={TOTP_STEP}")


class Auth:
    def __init__(self, config_path, log=print):
        self.config_path = config_path
        self.log = log
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(MAX_HASHING)
        self.sessions = {}            # jeton -> {role, id, name, expires, created, external}
        self.ip_fails = {}            # ip -> {fails, level, until}
        self.acct_fails = {}          # (compte, externe) -> [horodatages d'échecs]
        self.acct_until = {}          # (compte, externe) -> fin de blocage
        self.last_totp = {}           # compte -> dernière période TOTP acceptée (anti-rejeu)
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
                                                 "hash": stored, "plain": bool(a.get("pin") and not a.get("pinHash")),
                                                 "totp": a.get("totpSecret")}
        with self.lock:
            for k, acc in accounts.items():
                old = self.accounts.get(k)
                if old and (old["hash"] != acc["hash"] or old["totp"] != acc["totp"]):
                    self._drop_sessions(k)
            for k in set(self.accounts) - set(accounts):
                self._drop_sessions(k)
            self.accounts, self.cfg, self._mtime = accounts, cfg, mtime

    def _drop_sessions(self, key):
        role, ident = key.split(":", 1)
        for t in [t for t, s in self.sessions.items() if s["role"] == role and s["id"] == ident]:
            del self.sessions[t]

    def totp_mode(self):
        """'external' (défaut) : second facteur exigé hors du réseau interne ; 'always' ; 'never'."""
        mode = self.cfg.get("totp", "external")
        return mode if mode in ("external", "always", "never") else "external"

    def needs_totp(self, external):
        mode = self.totp_mode()
        return mode == "always" or (mode == "external" and external)

    def ttl(self, role, external=False):
        hours = self.cfg.get("sessionHoursExternal" if external else "sessionHours") or {}
        default = {"nurse": 4, "admin": 1} if external else {"nurse": 12, "admin": 2}
        return int(float(hours.get(role, default[role])) * 3600)

    def public_accounts(self, external=False):
        """Ce que l'écran de connexion peut savoir. Depuis l'extérieur, aucun nom de compte n'est donné
        (on saisit son identifiant) : on ne livre pas la liste des cibles."""
        out = {"hidden": bool(external), "totp": self.needs_totp(external), "nurses": [], "admins": []}
        if not external:
            out["nurses"] = [{"id": a["id"], "name": a["name"]} for a in self.accounts.values() if a["role"] == "nurse"]
            out["admins"] = [{"id": a["id"], "name": a["name"]} for a in self.accounts.values() if a["role"] == "admin"]
        else:
            out["hasAdmins"] = any(a["role"] == "admin" for a in self.accounts.values())
        return out

    def warnings(self):
        out = []
        for k, a in self.accounts.items():
            if not a["hash"]:
                out.append(f"{k} : aucun code défini (connexion impossible) - voir deploy/pins.py")
            elif a["plain"]:
                out.append(f"{k} : code en clair dans config.json - utilisez deploy/pins.py (code haché)")
            if a["hash"] and not a["totp"] and self.totp_mode() != "never":
                out.append(f"{k} : pas de second facteur - connexion impossible depuis l'extérieur "
                           f"(python3 deploy/pins.py totp {k})")
        if not any(a["role"] == "admin" for a in self.accounts.values()):
            out.append("aucun compte administration : les statistiques ne seront accessibles qu'aux infirmières (les leurs)")
        return out

    # ------------------------------------------------------------ connexion
    def _locked(self, ip, key, external, now):
        e = self.ip_fails.get(ip)
        if e and e["until"] > now:
            return int(e["until"] - now) + 1
        until = self.acct_until.get((key, external), 0)
        if until > now:
            return int(until - now) + 1
        return 0

    def _fail(self, ip, key, external, now):
        e = self.ip_fails.setdefault(ip, {"fails": 0, "level": 0, "until": 0})
        e["fails"] += 1
        if e["fails"] >= IP_MAX_FAILS:
            e["fails"], e["level"] = 0, e["level"] + 1
            e["until"] = now + min(IP_LOCK_BASE * 2 ** (e["level"] - 1), IP_LOCK_MAX)
            self.log(f"[auth] adresse {ip} bloquée {int(e['until'] - now)} s (échecs répétés)")
        k = (key, external)
        max_fails, lock_for = ACCOUNT_LIMITS[external]
        hist = [t for t in self.acct_fails.get(k, []) if t > now - ACCOUNT_WINDOW] + [now]
        self.acct_fails[k] = hist
        if len(hist) >= max_fails:
            self.acct_until[k] = now + lock_for
            self.acct_fails[k] = []
            self.log(f"[auth] compte {key} verrouillé {lock_for} s pour les connexions "
                     f"{'externes' if external else 'internes'} (trop d'échecs, toutes adresses)")

    def login(self, role, ident, pin, ip, external=False, code=None):
        """Retourne (ok, data) ; data = infos de session ou {'error', 'retry'}.
        external : la requête vient de l'extérieur du réseau interne ; code : code du second facteur."""
        self.reload()
        key = f"{role}:{ident}"
        where = "externe" if external else "interne"
        now = time.time()
        with self.lock:
            wait = self._locked(ip, key, external, now)
        if wait:
            return False, {"error": "Trop de tentatives. Réessayez dans %d min %02d s." % (wait // 60, wait % 60), "retry": wait}
        acc = self.accounts.get(key)
        pin = str(pin or "")[:128]
        # le hachage est coûteux : au plus MAX_HASHING en même temps, sinon on répond « occupé »
        if not self.slots.acquire(timeout=3):
            return False, {"error": "Serveur occupé, réessayez dans un instant.", "retry": 5}
        try:
            good = verify_pin(pin, acc["hash"] if acc and acc["hash"] else _DUMMY)   # même durée que le compte existe ou non
        finally:
            self.slots.release()
        need_totp = self.needs_totp(external)
        step = None
        if need_totp:                                    # toujours évalué : pas de différence de temps
            secret = (acc or {}).get("totp") or "A" * 32
            step = totp_check(secret, code, after=self.last_totp.get(key))
            if not (acc and acc.get("totp")):
                step = None
        with self.lock:
            ok = bool(acc and acc["hash"] and good and (step is not None or not need_totp))
            if not ok:
                self._fail(ip, key, external, now)
                if acc and acc["hash"] and good and need_totp and not acc.get("totp"):
                    self.log(f"[auth] refus {key} depuis {ip} ({where}) : second facteur non configuré")
                else:
                    self.log(f"[auth] échec de connexion {key} depuis {ip} ({where})")
                if need_totp:                            # message identique quel que soit l'élément faux
                    return False, {"error": "Identifiant, code ou code de l'application incorrect."}
                return False, {"error": "Code incorrect." if acc and acc["hash"] else
                               "Ce compte n'est pas activé : aucun code défini."}
            if need_totp:
                self.last_totp[key] = step
            self.ip_fails.pop(ip, None)
            self.acct_fails.pop((key, external), None)
            token = secrets.token_urlsafe(32)
            ttl = self.ttl(role, external)
            self.sessions[token] = {"role": role, "id": ident, "name": acc["name"], "created": now,
                                    "expires": now + ttl, "external": external}
            self.log(f"[auth] connexion {key} depuis {ip} ({where})" + (" avec second facteur" if need_totp else ""))
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
            if s["expires"] < now or s["created"] + SESSION_MAX_AGE[s["external"]] < now:
                del self.sessions[token]
                return None
            s["expires"] = now + self.ttl(s["role"], s["external"])        # session glissante
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
