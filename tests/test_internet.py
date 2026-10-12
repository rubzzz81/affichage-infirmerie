#!/usr/bin/env python3
"""Tests du durcissement pour une exposition sur Internet.

À lancer sur le Pi, dans une COPIE du dossier de l'application dont le nom contient « test » :

    cp -r ~/cabinet ~/cabinet-test && cd ~/cabinet-test && python3 tests/test_internet.py

Le test remplace config.json et certs/ de ce dossier, déclare « externe » TOUT ce qui n'est pas
192.0.2.0/24 (donc le Pi lui-même, qui se connecte à sa propre adresse), puis vérifie ce qu'un
visiteur venu d'Internet peut ou ne peut pas faire. Codes et secrets de test aléatoires, jamais affichés.
"""
import hashlib, http.client, json, os, re, socket, ssl, subprocess, sys, time

D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if "test" not in os.path.basename(D).lower():
    sys.exit("Refusé : ce test remplace config.json et les certificats. Lancez-le dans une copie nommée '*test*'.")
os.chdir(D)
sys.path.insert(0, D)
import auth  # noqa: E402

IP = os.environ.get("CABINET_TEST_IP") or subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
HP, TP = 8090, 8453
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("OK     " if cond else "ÉCHEC  ") + name + (f"   [{detail}]" if detail and not cond else ""), flush=True)


# ------------------------------------------------------------------ préparation
for f in ("config.json", "data.db", "state.json", "server.log", "auth.log"):
    if os.path.exists(f):
        os.remove(f)
subprocess.run("rm -rf certs", shell=True)
SECRET = {"broux": auth.new_totp_secret(), "direction": auth.new_totp_secret()}     # glinka : pas de second facteur
cfg = {"school": "Test", "title": "T", "port": HP, "domain": "pitest.example", "httpBind": "127.0.0.1",
       "nurses": [{"id": "broux", "name": "Mme B", "hours": {"default": ["00:00", "23:59"]}, "totpSecret": SECRET["broux"]},
                  {"id": "glinka", "name": "Mme G", "hours": {"default": ["00:00", "23:59"]}}],
       "admins": [{"id": "direction", "name": "Direction", "totpSecret": SECRET["direction"]}],
       "internalNetworks": ["10.0.0.0/8"], "externalAddresses": [IP],
       "sessionHoursExternal": {"nurse": 0.002, "admin": 1},
       "limits": {"perIpExternal": 6, "sseExternal": 2}}
json.dump(cfg, open("config.json", "w"))
subprocess.run(["sh", "deploy/setup-https.sh"], check=True, capture_output=True)
cfg = json.load(open("config.json")); cfg["tls"]["port"] = TP; json.dump(cfg, open("config.json", "w"), indent=2)
out = subprocess.run([sys.executable, "deploy/pins.py", "generate"], capture_output=True, text=True).stdout
PIN = dict(re.findall(r"^\s+(\S+:\S+)\s+.*?\s(\d{6})\s*$", out, re.M))

# --- 0. Qui est « interne » ? (fonctions du serveur, avec la configuration de test)
import server as srvmod  # noqa: E402
gw = srvmod.default_gateway()
check("la passerelle par défaut est lue dans la table de routage", gw is not None, str(gw))
check("la passerelle (le pare-feu) est TOUJOURS externe, même si son adresse est privée", gw is None or srvmod.is_internal(gw) is False)
check("une adresse privée du réseau interne est interne", srvmod.is_internal("10.5.35.50") is True)
check("« externalAddresses » : l'adresse listée est externe même dans une plage privée", srvmod.is_internal(IP) is False)
check("une adresse publique est externe", srvmod.is_internal("45.15.220.130") is False)
check("une IPv4 notée en IPv6 (::ffff:) est traitée comme son IPv4", srvmod.is_internal("::ffff:10.5.35.50") is True)
check("une adresse illisible est externe, par prudence", srvmod.is_internal("n'importe-quoi") is False)

srv = subprocess.Popen([sys.executable, "server.py"], env=dict(os.environ, PORT=str(HP), CABINET_NO_MDNS="1"),
                       stdout=open("server.log", "w"), stderr=subprocess.STDOUT)
time.sleep(2.5)
CTX = ssl.create_default_context(cafile="certs/ca.crt")


def req(method, path, body=None, cookie=None, headers=None, raw=False):
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = cookie
    data = None
    if body is not None:
        data = json.dumps(body)
        h.setdefault("Content-Type", "application/json")
    c = http.client.HTTPSConnection(IP, TP, context=CTX, timeout=10)
    c.request(method, path, data, h)
    r = c.getresponse()
    txt = r.read().decode("utf-8", "replace") if not raw else ""
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    try:
        js = json.loads(txt)
    except ValueError:
        js = None
    return r.status, hdrs, js, txt


def code_now(who, shift=0):
    return auth.totp_at(SECRET[who], int(time.time() // 30) + shift)


def login(role, ident, pin, code=None):
    body = {"role": role, "id": ident, "pin": pin}
    if code is not None:
        body["code"] = code
    return req("POST", "/api/login", body)


GENERIC = "Identifiant, code ou code de l'application incorrect."

try:
    # --- 1. Rien de lisible sans connexion
    s, h, j, t = req("GET", "/api/state")
    check("externe : /api/state sans connexion -> 401", s == 401, str(s))
    s, h, j, t = req("GET", "/api/events")
    check("externe : /api/events (temps réel) sans connexion -> 401", s == 401, str(s))
    s, h, j, t = req("GET", "/")
    check("externe : l'écran public renvoie vers la connexion (302)", s == 302 and "admin" in h.get("location", ""), f"{s} {h.get('location')}")
    s, h, j, t = req("GET", "/api/accounts")
    check("externe : la liste des comptes est masquée", s == 200 and j.get("hidden") is True and j.get("nurses") == [] and j.get("admins") == [] and "Mme" not in t, t[:100])
    check("externe : le serveur annonce que le second facteur est exigé", j.get("totp") is True)
    for p in ("/ca.crt", "/certificat", "/api/ca"):
        s, h, j, t = req("GET", p)
        check(f"externe : {p} n'existe pas", s == 404, str(s))
    s, h, j, t = req("GET", "/admin")
    check("externe : la page de connexion s'affiche (aucune donnée)", s == 200 and "Ma présence" in t)
    s, h, j, t = req("GET", "/logo")
    check("externe : le logo reste visible (page de connexion)", s == 200)
    s, h, j, t = req("GET", "/api/stats")
    check("externe : /api/stats sans connexion -> 401", s == 401)

    # --- 2. Connexion avec second facteur
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"])
    check("externe : bon code SANS second facteur -> refusé (message neutre)", s == 401 and j.get("error") == GENERIC, f"{s} {j}")
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"], "000000")
    check("externe : bon code + mauvais second facteur -> refusé (message neutre)", s == 401 and j.get("error") == GENERIC)
    s, h, j, t = login("nurse", "broux", "000111", code_now("broux"))
    check("externe : mauvais code + bon second facteur -> refusé (même message)", s == 401 and j.get("error") == GENERIC)
    s, h, j, t = login("nurse", "inconnue", "123456", "123456")
    check("externe : compte inexistant -> même message", s == 401 and j.get("error") == GENERIC)

    step = int(time.time() // 30)
    good = code_now("broux")
    spaced = good[:3] + " " + good[3:]
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"], spaced)
    check("externe : bon code + bon second facteur -> connecté", s == 200 and j.get("ok") and "set-cookie" in h, f"{s} {j}")
    NB = h.get("set-cookie", "").split(";")[0]
    check("externe : cookie Secure + HttpOnly + SameSite=Strict", all(x in h.get("set-cookie", "") for x in ("Secure", "HttpOnly", "SameSite=Strict")))
    s, h, j, t = login("nurse", "glinka", PIN["nurse:glinka"], "123456")
    check("externe : compte SANS second facteur -> connexion impossible", s == 401 and j.get("error") == GENERIC, f"{s}")
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"], good)
    check("anti-rejeu : le même code du second facteur ne sert qu'une fois", s == 401, str(s))
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"], code_now("broux", -1))
    check("anti-rejeu : un code d'une période PLUS ANCIENNE est refusé", s == 401, str(s))
    s, h, j, t = login("admin", "direction", PIN["admin:direction"], code_now("direction"))
    check("externe : administration avec second facteur -> connectée", s == 200 and j.get("role") == "admin")
    AD = h.get("set-cookie", "").split(";")[0]

    # --- 3. Avec session : accès normal, sans fuite
    s, h, j, t = req("GET", "/api/me", cookie=NB)
    check("session externe : /api/me indique « externe »", s == 200 and j.get("external") is True)
    s, h, j, t = req("GET", "/api/state", cookie=NB)
    check("avec session : /api/state accessible", s == 200 and "nurses" in j)
    s, h, j, t = req("POST", "/api/set", {"status": "present"}, cookie=NB)
    check("avec session : l'infirmière pilote son panneau", s == 200, str(s))
    s, h, j, t = req("POST", "/api/set", {"id": "glinka", "status": "present"}, cookie=NB)
    check("avec session : pas le panneau d'une autre (403)", s == 403)
    s, h, j, t = req("GET", "/api/stats?nurse=glinka", cookie=NB)
    check("avec session : pas les statistiques d'une autre (403)", s == 403)
    s, h, j, t = req("GET", "/api/stats", cookie=AD)
    check("administration externe : voit toutes les statistiques", s == 200 and len(j["nurses"]) == 2)
    s, h, j, t = req("POST", "/api/set", {"id": "broux", "status": "absent"}, cookie=AD)
    check("administration externe : lecture seule (403)", s == 403)

    # --- 4. En-têtes
    s, h, j, t = req("GET", "/admin")
    check("HSTS : le navigateur est forcé en HTTPS", "max-age=31536000" in h.get("strict-transport-security", ""))
    check("l'en-tête Server ne révèle rien (pas de version de Python)", h.get("server", "") == "cabinet" and "python" not in str(h).lower(), h.get("server"))
    check("en-têtes d'isolation (COOP, CORP, Permissions-Policy)", all(k in h for k in ("cross-origin-opener-policy", "cross-origin-resource-policy", "permissions-policy")))

    # --- 5. Port HTTP : seulement le Pi lui-même
    refused = False
    try:
        socket.create_connection((IP, HP), timeout=3).close()
    except OSError:
        refused = True
    check("httpBind : le port HTTP n'est pas joignable par l'adresse du réseau", refused)
    try:
        socket.create_connection(("127.0.0.1", HP), timeout=3).close()
        local_ok = True
    except OSError:
        local_ok = False
    check("httpBind : mais il reste joignable depuis le Pi (kiosque)", local_ok)
    c = http.client.HTTPConnection("127.0.0.1", HP, timeout=5); c.request("GET", "/api/state"); r = c.getresponse(); r.read(); c.close()
    check("le kiosque (local) lit l'état sans connexion", r.status == 200, str(r.status))

    # --- 6. Limites de connexions (6 par adresse externe, 2 flux temps réel)
    held = []
    for _ in range(9):
        try:
            sk = socket.create_connection((IP, TP), timeout=3)
            held.append(sk)
        except OSError:
            pass
    time.sleep(0.8)
    closed = 0
    for sk in held:
        sk.settimeout(0.4)
        try:
            if sk.recv(1) == b"":
                closed += 1
        except socket.timeout:
            pass
        except OSError:
            closed += 1
    for sk in held:
        sk.close()
    check("limite par adresse : au-delà de 6 connexions simultanées, les suivantes sont coupées", closed >= 2, f"{closed} coupées sur {len(held)}")
    time.sleep(1)
    s, h, j, t = req("GET", "/api/accounts")
    check("après la libération des connexions, le service répond de nouveau", s == 200, str(s))

    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"], code_now("broux", 1))
    NB2 = h.get("set-cookie", "").split(";")[0]
    streams, codes = [], []
    for _ in range(3):
        c = http.client.HTTPSConnection(IP, TP, context=CTX, timeout=5)
        c.request("GET", "/api/events", headers={"Cookie": NB2})
        r = c.getresponse()
        codes.append(r.status)
        streams.append((c, r))
    for c, r in streams:
        c.close()
    check("limite des flux temps réel : 2 par adresse externe, le 3e est refusé (429)", codes == [200, 200, 429], str(codes))

    # --- 7. Verrouillages séparés interne / externe (test direct du module d'authentification)
    A = auth.Auth("config.json", log=lambda m: None)
    pinb = PIN["nurse:broux"]
    for i in range(10):
        A.login("nurse", "broux", "faux-code", f"198.51.100.{i + 1}", external=True, code="000000")
    ok, data = A.login("nurse", "broux", pinb, "198.51.100.200", external=True, code=auth.totp_at(SECRET["broux"], int(time.time() // 30) + 1))
    check("10 échecs depuis Internet : le compte est verrouillé POUR LES CONNEXIONS EXTERNES", not ok and data.get("retry", 0) > 0, str(data))
    ok, data = A.login("nurse", "broux", pinb, "10.5.35.50", external=False)
    check("...mais l'infirmière peut toujours se connecter depuis le lycée (pas de déni de service)", ok, str(data))
    ok, data = A.login("nurse", "broux", pinb, "10.5.35.51", external=False, code=None)
    check("connexion interne : le second facteur n'est pas exigé (mode « external »)", ok)
    A_always = auth.Auth("config.json", log=lambda m: None)
    A_always.cfg["totp"] = "always"
    ok, data = A_always.login("nurse", "broux", pinb, "10.5.35.52", external=False)
    check("mode « always » : le second facteur est aussi exigé en interne", not ok and data.get("error") == GENERIC, str(data))

    # --- 8. Renouvellement du certificat sans redémarrage
    def server_cert_hash():
        raw = socket.create_connection((IP, TP), timeout=5)
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        with ctx.wrap_socket(raw, server_hostname=IP) as s2:
            return hashlib.sha256(s2.getpeercert(binary_form=True)).hexdigest()
    before = server_cert_hash()
    subprocess.run(["sh", "deploy/setup-https.sh"], check=True, capture_output=True)
    changed = False
    for _ in range(40):
        time.sleep(1)
        if server_cert_hash() != before:
            changed = True
            break
    check("certificat renouvelé : pris en compte sans redémarrage (≤ 40 s)", changed)
    s, h, j, t = req("GET", "/admin")
    check("...et le site reste joignable avec le nouveau certificat (autorité inchangée)", s == 200)

    # --- 9. Expiration plus courte des sessions externes (7 s configurées)
    time.sleep(8.5)
    s, h, j, t = req("GET", "/api/me", cookie=NB2)
    check("session externe : expire après une courte inactivité", s == 401, str(s))
finally:
    srv.terminate()
    time.sleep(0.5)
    log = open("server.log", encoding="utf-8", errors="replace").read()
    print("\n--- extrait du journal :")
    for line in log.splitlines():
        if "[sécurité]" in line or "second facteur" in line or "Traceback" in line or "bloquée" in line:
            print("  " + line[:150])
    leaked = [x for x in list(PIN.values()) + list(SECRET.values()) if x in log or x in open("auth.log").read()]
    check("journaux : aucun code ni secret n'y figure", not leaked)

print(f"\nRésultat : {sum(results)}/{len(results)} vérifications réussies")
sys.exit(0 if all(results) else 1)
