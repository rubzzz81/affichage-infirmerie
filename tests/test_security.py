#!/usr/bin/env python3
"""Tests de sécurité de l'application (serveur, droits, HTTPS, blocage des essais).

À lancer sur le Pi, dans une COPIE du dossier de l'application dont le nom contient « test » :

    cp -r ~/cabinet ~/cabinet-test && cd ~/cabinet-test && python3 tests/test_security.py

Le test REMPLACE config.json, certs/, data.db et state.json de ce dossier, lance un serveur
de test sur les ports 8090 (HTTP) et 8453 (HTTPS) et utilise des codes aléatoires, gardés en
mémoire et jamais affichés. Il refuse de s'exécuter ailleurs que dans un dossier « *test* »
pour ne jamais toucher à la vraie installation. Variable facultative : CABINET_TEST_IP.
"""
import http.client, json, os, re, ssl, subprocess, sys, time

D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if "test" not in os.path.basename(D).lower():
    sys.exit("Refusé : ce test remplace config.json et les certificats. Lancez-le dans une copie nommée '*test*'.")
os.chdir(D)
IP = os.environ.get("CABINET_TEST_IP") or subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
HP, TP = 8090, 8453
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("OK     " if cond else "ÉCHEC  ") + name + (f"   [{detail}]" if detail and not cond else ""), flush=True)


# --- configuration de test + HTTPS + codes aléatoires
cfg = {"school": "Test", "title": "T", "port": HP,
       "nurses": [{"id": "broux", "name": "Mme B", "hours": {"default": ["00:00", "23:59"]}},
                  {"id": "glinka", "name": "Mme G", "hours": {"default": ["00:00", "23:59"]}}],
       "admins": [{"id": "direction", "name": "Direction"}, {"id": "vide", "name": "Sans code"}],
       "sessionHours": {"nurse": 0.002, "admin": 2}, "domain": "pitest.example"}
json.dump(cfg, open("config.json", "w"))
subprocess.run(["sh", "deploy/setup-https.sh"], check=True, capture_output=True)
cfg = json.load(open("config.json"))
cfg["tls"]["port"] = TP
json.dump(cfg, open("config.json", "w"), indent=2)

# 'direction' et les infirmières reçoivent un code aléatoire ; le compte "vide" reste sans code
out = subprocess.run([sys.executable, "deploy/pins.py", "generate"], capture_output=True, text=True).stdout
PIN = dict(re.findall(r"^\s+(\S+:\S+)\s+.*?\s(\d{6})\s*$", out, re.M))
cfg = json.load(open("config.json"))
cfg["admins"] = [a for a in cfg["admins"]]
check("pins.py generate : 3 codes générés (2 infirmières + direction), compte 'vide' inclus ou non", len(PIN) >= 3, str(len(PIN)))
raw = open("config.json").read()
check("config.json : aucun code en clair", not any(p in raw for p in PIN.values()))
check("config.json : droits 600", oct(os.stat("config.json").st_mode & 0o777) == "0o600", oct(os.stat("config.json").st_mode & 0o777))
# on retire le code du compte 'vide' pour tester un compte non activé
cfg = json.load(open("config.json"))
for a in cfg["admins"]:
    if a["id"] == "vide":
        a.pop("pinHash", None)
PIN.pop("admin:vide", None)
json.dump(cfg, open("config.json", "w"), indent=2)

env = dict(os.environ, PORT=str(HP))
log = open("server.log", "w")
srv = subprocess.Popen([sys.executable, "server.py"], env=env, stdout=log, stderr=subprocess.STDOUT)
time.sleep(2.5)

CTX = ssl.create_default_context(cafile="certs/ca.crt")      # vérifie la chaîne ET l'adresse IP (SAN)


def req(method, path, body=None, cookie=None, tls=True, headers=None, raw_body=None):
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = cookie
    data = raw_body
    if body is not None:
        data = json.dumps(body)
        h.setdefault("Content-Type", "application/json")
    c = (http.client.HTTPSConnection(IP, TP, context=CTX, timeout=10) if tls
         else http.client.HTTPConnection("127.0.0.1", HP, timeout=10))
    c.request(method, path, data, h)
    r = c.getresponse()
    txt = r.read().decode("utf-8", "replace")
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    try:
        js = json.loads(txt)
    except ValueError:
        js = None
    return r.status, hdrs, js, txt


def login(role, ident, pin, **kw):
    return req("POST", "/api/login", {"role": role, "id": ident, "pin": pin}, **kw)


def cookie_of(h):
    return h["set-cookie"].split(";")[0]


try:
    # --- 1. HTTPS obligatoire pour tout ce qui est protégé
    s, h, j, t = req("GET", "/api/state", tls=False)
    check("HTTP : /api/state reste public (écran du couloir)", s == 200 and j and "nurses" in j)
    check("HTTP : /api/state ne contient aucune information de compte", "pin" not in t.lower() and "hash" not in t.lower())
    s, h, j, t = req("GET", "/admin", tls=False)
    check("HTTP : /admin redirige vers HTTPS", s == 302 and h.get("location", "").startswith(f"https://127.0.0.1:{TP}/admin"), f"{s} {h.get('location')}")
    s, h, j, t = req("GET", "/stats", tls=False)
    check("HTTP : /stats redirige vers HTTPS", s == 302 and h.get("location", "").startswith("https://"))
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"], tls=False)
    check("HTTP : la connexion est refusée (code jamais en clair)", s == 403 and "set-cookie" not in h, str(s))
    s, h, j, t = req("GET", "/api/stats", tls=False)
    check("HTTP : /api/stats refusé", s == 403)
    s, h, j, t = req("GET", "/ca.crt", tls=False)
    check("/ca.crt téléchargeable (autorité locale)", s == 200 and "BEGIN CERTIFICATE" in t)

    # --- 2. Connexion
    s, h, j, t = req("GET", "/api/me")
    check("HTTPS : /api/me sans session -> 401", s == 401)
    s, h, j, t = req("GET", "/api/accounts")
    check("/api/accounts : noms seulement, pas de codes", s == 200 and "hash" not in t.lower() and "pin" not in t.lower() and len(j["nurses"]) == 2)
    s, h, j, t = login("nurse", "broux", "000111")
    check("mauvais code -> 401, pas de cookie", s == 401 and "set-cookie" not in h, str(s))
    s, h, j, t = login("nurse", "inconnue", "123456")
    check("compte inexistant -> même réponse qu'un mauvais code (401)", s == 401, str(s))
    s, h, j, t = login("admin", "vide", "123456")
    check("compte sans code défini -> connexion impossible", s == 401 and "activé" in (j or {}).get("error", ""), str(s))
    s, h, j, t = login("nurse", "direction", PIN["admin:direction"])
    check("le code d'un admin ne marche pas comme compte infirmière", s == 401)
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"])
    check("bon code -> 200 + cookie", s == 200 and j.get("ok") and "set-cookie" in h, str(s))
    ck = h.get("set-cookie", "")
    check("cookie : HttpOnly", "HttpOnly" in ck)
    check("cookie : SameSite=Strict", "SameSite=Strict" in ck)
    check("cookie : Secure (HTTPS)", "Secure" in ck)
    check("cookie : jeton aléatoire long", len(ck.split("=")[1].split(";")[0]) >= 40)
    NB = cookie_of(h)
    s, h, j, t = req("GET", "/api/me", cookie=NB)
    check("session valide : /api/me -> infirmière broux", s == 200 and j["role"] == "nurse" and j["id"] == "broux")
    check("en-têtes de sécurité présents", "frame-ancestors" in h.get("content-security-policy", "") and h.get("x-frame-options") == "DENY" and h.get("x-content-type-options") == "nosniff")


    # --- 2b. Non-régression : un POST refusé ne doit pas corrompre la requête suivante (connexion persistante)
    c = http.client.HTTPSConnection(IP, TP, context=CTX, timeout=10)
    c.request("POST", "/api/logout", json.dumps({}), {"Content-Type": "application/json"})
    r1 = c.getresponse(); r1.read()
    c.request("GET", "/api/accounts")
    r2 = c.getresponse(); r2.read()
    check("connexion persistante : requête après un POST refusé/traité -> 200 (pas de 501)", r1.status == 200 and r2.status == 200, f"{r1.status}/{r2.status}")
    c.request("POST", "/api/set", json.dumps({"id": "broux", "status": "present"}), {"Content-Type": "application/json", "Origin": "https://evil.example"})
    r3 = c.getresponse(); r3.read()
    c.request("GET", "/api/accounts")
    r4 = c.getresponse(); r4.read()
    check("connexion persistante : après un POST refusé (Origin étrangère) -> 200", r3.status == 403 and r4.status == 200, f"{r3.status}/{r4.status}")
    c.close()
    # --- 2c. Sous-domaines : admin.* / stats.* / domaine principal
    AH, SH, PH = f"admin.pitest.example:{TP}", f"stats.pitest.example:{TP}", f"pitest.example:{TP}"
    AU, SU = f"https://admin.pitest.example:{TP}/", f"https://stats.pitest.example:{TP}/"
    s, h, j, t = req("GET", "/", headers={"Host": AH})
    check("admin.* : la racine sert le panneau des infirmières", s == 200 and "Ma présence" in t, str(s))
    s, h, j, t = req("GET", "/", headers={"Host": SH})
    check("stats.* : la racine sert les statistiques", s == 200 and "<title>Statistiques" in t, str(s))
    s, h, j, t = req("GET", "/", headers={"Host": PH})
    check("domaine principal : la racine sert l'écran public", s == 200 and "<title>Cabinet d'infirmières</title>" in t, str(s))
    s, h, j, t = req("GET", "/admin", headers={"Host": PH})
    check("domaine principal /admin -> redirige vers admin.*", s == 302 and h.get("location") == AU, f"{s} {h.get('location')}")
    s, h, j, t = req("GET", "/stats", headers={"Host": PH})
    check("domaine principal /stats -> redirige vers stats.*", s == 302 and h.get("location") == SU, f"{s} {h.get('location')}")
    s, h, j, t = req("GET", "/stats", headers={"Host": AH})
    check("admin.* /stats -> redirige vers stats.*", s == 302 and h.get("location") == SU)
    s, h, j, t = req("GET", "/admin", headers={"Host": SH})
    check("stats.* /admin -> redirige vers admin.*", s == 302 and h.get("location") == AU)
    s, h, j, t = req("GET", "/admin")
    check("accès par adresse IP : /admin fonctionne toujours (compatibilité)", s == 200 and "Ma présence" in t, str(s))
    s, h, j, t = req("GET", "/", headers={"Host": f"admin.pitest.example:{HP}"}, tls=False)
    check("HTTP sur admin.* -> redirige vers HTTPS (même nom)", s == 302 and h.get("location") == AU, f"{s} {h.get('location')}")
    s, h, j, t = req("GET", "/api/state", tls=False)
    check("/api/state : adresses des trois sites", j.get("urls", {}).get("admin") == AU and j["urls"]["stats"] == SU and j["urls"]["public"] == f"https://pitest.example:{TP}/", str(j.get("urls")))
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"], headers={"Host": AH})
    check("connexion sur admin.* : cookie partagé (Domain=pitest.example)", s == 200 and "Domain=pitest.example" in h.get("set-cookie", ""), h.get("set-cookie", "")[:120])
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"], headers={"Host": AH, "Origin": SU.rstrip("/")})
    check("connexion avec l'origine d'un AUTRE sous-domaine -> 403", s == 403, str(s))
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"], headers={"Host": AH, "Origin": AU.rstrip("/")})
    check("connexion avec l'origine du même site -> 200", s == 200, str(s))
    s, h, j, t = login("nurse", "broux", PIN["nurse:broux"])
    check("connexion par adresse IP : pas d'attribut Domain (cookie lié à l'hôte)", s == 200 and "Domain=" not in h.get("set-cookie", ""))
    s, h, j, t = req("GET", "/certificat", tls=False)
    check("/certificat (aide à l'installation) accessible sans compte", s == 200 and "certificat" in t.lower())
    s, h, j, t = req("GET", "/api/ca", tls=False)
    check("/api/ca : empreinte SHA-256 de l'autorité", s == 200 and re.fullmatch(r"([0-9A-F]{2}:){31}[0-9A-F]{2}", j.get("fingerprint", "")) is not None)
    # --- 2d. Noms par défaut d'un domaine « .local » (annoncés en mDNS, à un seul niveau)
    keep = open("config.json").read()
    c2 = json.loads(keep); c2["domain"] = "pi-test.local"; c2.pop("hosts", None)
    open("config.json", "w").write(json.dumps(c2))
    names = subprocess.run([sys.executable, "server.py", "--print-hosts"], capture_output=True, text=True).stdout.split()
    open("config.json", "w").write(keep)
    check("domaine .local : noms à un seul niveau (résolus par Windows en mDNS)", names == ["pi-test.local", "pi-test-admin.local", "pi-test-stats.local"], str(names))

    # --- 3. Droits d'une infirmière
    s, h, j, t = req("POST", "/api/set", {"id": "broux", "status": "present"}, cookie=NB)
    check("infirmière : modifie SON panneau -> 200", s == 200, f"{s} {t[:80]}")
    s, h, j, t = req("POST", "/api/set", {"status": "consult"}, cookie=NB)
    check("infirmière : sans préciser l'id -> son propre panneau", s == 200)
    s, h, j, t = req("POST", "/api/set", {"id": "glinka", "status": "present"}, cookie=NB)
    check("infirmière : modifier le panneau d'une AUTRE -> 403", s == 403, str(s))
    s, h, j, t = req("GET", "/api/state", tls=False)
    st = {n["id"]: n["status"] for n in j["nurses"]}
    check("l'état de l'autre infirmière n'a pas bougé", st["glinka"] == "absent" and st["broux"] == "consult", str(st))
    s, h, j, t = req("POST", "/api/set", {"id": "broux", "status": "present"})
    check("sans session : modification refusée (401)", s == 401)
    s, h, j, t = req("POST", "/api/set", {"id": "broux", "status": "present", "pin": PIN["nurse:broux"]})
    check("l'ancien champ 'pin' dans la requête ne donne aucun droit", s == 401)
    s, h, j, t = req("POST", "/api/set", {"id": "broux", "status": "present"}, cookie="cabinet_session=abcdef")
    check("jeton inventé -> 401", s == 401)
    s, h, j, t = req("POST", "/api/set", raw_body='{"id":"broux","status":"present"}', cookie=NB, headers={"Content-Type": "text/plain"})
    check("POST sans Content-Type JSON -> 400 (anti-CSRF)", s == 400, str(s))
    s, h, j, t = req("POST", "/api/set", {"id": "broux", "status": "present"}, cookie=NB, headers={"Origin": "https://evil.example"})
    check("POST avec Origin étrangère -> 403 (CSRF)", s == 403, str(s))
    s, h, j, t = req("POST", "/api/set", {"id": "broux", "status": "zzz"}, cookie=NB)
    check("statut invalide -> 400", s == 400)

    # --- 4. Statistiques
    s, h, j, t = req("GET", "/api/stats")
    check("stats sans session -> 401", s == 401)
    s, h, j, t = req("GET", "/api/stats.csv")
    check("export CSV sans session -> 401", s == 401)
    s, h, j, t = req("GET", "/api/stats", cookie=NB)
    check("infirmière : stats limitées aux siennes", s == 200 and [n["id"] for n in j["nurses"]] == ["broux"], f"{s}")
    s, h, j, t = req("GET", "/api/stats?nurse=glinka", cookie=NB)
    check("infirmière : stats d'une autre -> 403", s == 403)
    s, h, j, t = req("GET", "/api/events.csv?nurse=glinka", cookie=NB)
    check("infirmière : détail CSV d'une autre -> 403", s == 403)
    s, h, j, t = req("GET", "/api/events.csv", cookie=NB)
    check("infirmière : son détail CSV ne contient aucune autre infirmière", s == 200 and "Mme G" not in t)
    s, h, j, t = login("admin", "direction", PIN["admin:direction"])
    check("administration : connexion", s == 200 and j["role"] == "admin")
    AD = cookie_of(h)
    s, h, j, t = req("GET", "/api/stats", cookie=AD)
    check("administration : voit toutes les infirmières", s == 200 and sorted(n["id"] for n in j["nurses"]) == ["broux", "glinka"])
    s, h, j, t = req("GET", "/api/stats?nurse=glinka", cookie=AD)
    check("administration : filtre sur une infirmière", s == 200 and len(j["nurses"]) == 1)
    s, h, j, t = req("POST", "/api/set", {"id": "broux", "status": "absent"}, cookie=AD)
    check("administration : ne peut PAS piloter un panneau (lecture seule)", s == 403, str(s))

    # --- 5. Changement de code = sessions fermées
    subprocess.run([sys.executable, "deploy/pins.py", "generate", "--all"], capture_output=True)
    time.sleep(0.2)
    s, h, j, t = req("GET", "/api/me", cookie=AD)
    check("code changé -> les sessions ouvertes du compte sont fermées", s == 401, str(s))
    s, h, j, t = login("admin", "direction", PIN["admin:direction"])
    check("l'ancien code ne fonctionne plus", s == 401, str(s))

    # --- 6. Déconnexion
    out = subprocess.run([sys.executable, "deploy/pins.py", "list"], capture_output=True, text=True).stdout
    check("pins.py list : n'affiche aucun code", not re.search(r"\b\d{6}\b", out))

    # --- 7. Expiration de session (7 s configurées pour le test)
    PIN2 = dict(re.findall(r"^\s+(\S+:\S+)\s+.*?\s(\d{6})\s*$",
                subprocess.run([sys.executable, "deploy/pins.py", "generate", "--all"], capture_output=True, text=True).stdout, re.M))
    s, h, j, t = login("nurse", "glinka", PIN2["nurse:glinka"])
    GK = cookie_of(h)
    s, h, j, t = req("POST", "/api/logout", {}, cookie=GK)
    check("déconnexion -> 200 et cookie effacé", s == 200 and "Max-Age=0" in h.get("set-cookie", ""))
    s, h, j, t = req("GET", "/api/me", cookie=GK)
    check("après déconnexion, le jeton est invalide", s == 401)
    s, h, j, t = login("nurse", "glinka", PIN2["nurse:glinka"])
    GK = cookie_of(h)
    time.sleep(8.5)
    s, h, j, t = req("GET", "/api/me", cookie=GK)
    check("session expirée (inactivité) -> 401", s == 401, str(s))

    # --- 8. Blocage des tentatives (en dernier : bloque cette adresse)
    codes = []
    for i in range(7):
        s, h, j, t = login("nurse", "broux", f"99999{i}")
        codes.append(s)
    check("5 échecs puis blocage de l'adresse (429)", codes[:5] == [401] * 5 and 429 in codes[5:], str(codes))
    s, h, j, t = login("nurse", "broux", PIN2["nurse:broux"])
    check("pendant le blocage, même le BON code est refusé (429)", s == 429 and j.get("retry", 0) > 200, f"{s}")
    s, h, j, t = login("nurse", "glinka", PIN2["nurse:glinka"])
    check("le blocage vaut pour tous les comptes depuis cette adresse", s == 429)
    s, h, j, t = req("GET", "/api/state", tls=False)
    check("l'écran public reste disponible malgré le blocage", s == 200)
finally:
    srv.terminate()
    time.sleep(0.5)
    print("\n--- journal du serveur ([auth] / [sécurité]) :")
    for line in open("server.log", encoding="utf-8", errors="replace"):
        if line.startswith("[") or "Traceback" in line:
            print("  " + line.rstrip())
    leaked = [p for p in list(PIN.values()) + list(locals().get("PIN2", {}).values()) if p in open("server.log").read()]
    check("journal : aucun code n'y figure", not leaked)

print(f"\nRésultat : {sum(results)}/{len(results)} vérifications réussies")
sys.exit(0 if all(results) else 1)
