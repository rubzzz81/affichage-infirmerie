#!/usr/bin/env python3
"""Vérifie, DE L'EXTÉRIEUR, ce que le Pi expose. À lancer depuis une machine située HORS du réseau du
lycée (téléphone en 4G avec Termux, PC à la maison, serveur loué...). Python 3, bibliothèque standard.

    python3 check-exposure.py <adresse-ou-nom> [port-HTTPS]

    python3 check-exposure.py 45.15.220.130 8443
    python3 check-exposure.py infirmerie.lycee.fr 443 --verifie-certificat

Ce que le script contrôle :
  1. les ports : seul le port HTTPS doit répondre (jamais SSH 22, VNC 5900, HTTP 8080, Bureau à distance 3389...) ;
  2. ce qu'un inconnu peut lire : rien (état, statistiques, exports, liste des comptes, aide au certificat) ;
  3. le chiffrement : TLS 1.2 minimum, HSTS, en-têtes de sécurité, pas de version de logiciel affichée.

Il ne tente AUCUNE attaque (pas de devinette de code) ; il n'envoie que des requêtes de lecture.
Code de sortie : 0 si tout est conforme, 1 sinon.
"""
import http.client
import json
import socket
import ssl
import sys

host = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None
if not host:
    sys.exit(__doc__)
port = int(next((a for a in sys.argv[2:] if a.isdigit()), 8443))
verify = "--verifie-certificat" in sys.argv
problems, warnings = [], []


def ok(msg):
    print("  OK      " + msg)


def bad(msg):
    problems.append(msg)
    print("  PROBLÈME " + msg)


def info(msg):
    warnings.append(msg)
    print("  À SAVOIR " + msg)


def tcp_open(p, timeout=4):
    try:
        socket.create_connection((host, p), timeout=timeout).close()
        return True
    except OSError:
        return False


print(f"\n1. Ports joignables sur {host}")
sensitive = {22: "SSH (accès à distance au Pi)", 23: "Telnet", 80: "HTTP", 445: "partage Windows", 3389: "Bureau à distance Windows",
             5353: "mDNS (TCP)", 5900: "VNC (prise de contrôle de l'écran)", 8080: "HTTP de l'application", 5901: "VNC"}
if not tcp_open(port):
    bad(f"le port HTTPS {port} ne répond pas : la redirection de port ne fonctionne pas, ou le pare-feu bloque")
else:
    ok(f"le port HTTPS {port} répond")
for p, label in sensitive.items():
    if p == port:
        continue
    if tcp_open(p, 2.5):
        bad(f"le port {p} ({label}) est OUVERT depuis Internet : à fermer sur le pare-feu")
if not any("est OUVERT" in m for m in problems):
    ok("aucun autre port sensible n'est joignable (22, 23, 80, 445, 3389, 5900, 8080...)")


def https(path="/", method="GET", body=None, headers=None):
    ctx = ssl.create_default_context() if verify else ssl._create_unverified_context()
    c = http.client.HTTPSConnection(host, port, context=ctx, timeout=10)
    c.request(method, path, body, headers or {})
    r = c.getresponse()
    data = r.read().decode("utf-8", "replace")
    h = {k.lower(): v for k, v in r.getheaders()}
    ver = c.sock.version() if c.sock else None
    c.close()
    return r.status, h, data, ver


print("\n2. Ce qu'un inconnu (sans compte) peut lire")
try:
    s, h, t, tls = https("/admin")
    ok(f"la page de connexion répond ({tls})") if s == 200 else bad(f"/admin répond {s} (attendu 200 : page de connexion)")
    for path, label in (("/api/state", "l'état des infirmières"), ("/api/events", "le flux temps réel"),
                        ("/api/stats", "les statistiques"), ("/api/stats.csv", "l'export des statistiques"),
                        ("/api/events.csv", "l'export détaillé")):
        s, h, t, _ = https(path)
        ok(f"{label} : refusé ({s})") if s in (401, 403, 404) else bad(f"{label} : LISIBLE sans connexion (réponse {s})")
    s, h, t, _ = https("/")
    ok(f"l'écran public n'est pas servi aux inconnus (réponse {s})") if s in (301, 302, 401, 403, 404) \
        else bad(f"l'écran public est accessible sans connexion (réponse {s}) : il montre la présence du personnel")
    s, h, t, _ = https("/api/accounts")
    try:
        j = json.loads(t)
    except ValueError:
        j = {}
    if s == 200 and j.get("hidden") is True and not j.get("nurses") and not j.get("admins"):
        ok("la liste des comptes est masquée (on saisit son identifiant)")
    else:
        bad("la liste des comptes (noms du personnel) est visible depuis Internet")
    for path in ("/ca.crt", "/certificat", "/api/ca", "/static/../config.json", "/config.json", "/data.db", "/state.json", "/auth.log"):
        s, h, t, _ = https(path)
        ok(f"{path} : introuvable ({s})") if s in (400, 403, 404) else bad(f"{path} répond {s} : à vérifier")
    s, h, t, _ = https("/api/set", "POST", "{}", {"Content-Type": "application/json"})
    ok(f"modifier un état sans connexion : refusé ({s})") if s in (401, 403) else bad(f"/api/set sans connexion répond {s}")
except OSError as e:
    bad(f"impossible de se connecter en HTTPS : {e}")

print("\n3. Chiffrement et en-têtes")
try:
    s, h, t, tls = https("/admin")
    ok(f"version TLS négociée : {tls}") if tls in ("TLSv1.2", "TLSv1.3") else bad(f"version TLS faible : {tls}")

    ok("HSTS actif") if "max-age" in h.get("strict-transport-security", "") else bad("en-tête HSTS absent")
    for k in ("content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy"):
        ok(f"en-tête {k}") if k in h else bad(f"en-tête {k} absent")
    srv = h.get("server", "")
    ok(f"bannière du serveur discrète ({srv or 'absente'})") if "python" not in srv.lower() and "/" not in srv \
        else bad(f"la bannière du serveur révèle la version du logiciel : {srv}")
    if verify:
        ok("le certificat est valable pour ce nom, signé par une autorité reconnue")
    else:
        info("le certificat n'a pas été vérifié (ajoutez --verifie-certificat pour contrôler la chaîne et le nom)")
except ssl.SSLCertVerificationError as e:
    bad(f"le certificat n'est pas reconnu par cette machine : {e.verify_message}")
except OSError as e:
    bad(f"erreur TLS : {e}")

print(f"\nRésultat : {len(problems)} problème(s), {len(warnings)} remarque(s)")
if problems:
    print("À corriger avant de considérer l'accès Internet comme sûr.")
sys.exit(1 if problems else 0)
