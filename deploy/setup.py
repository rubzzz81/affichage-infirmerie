#!/usr/bin/env python3
"""Assistant de configuration : pose quelques questions et écrit config.json.

  python3 deploy/setup.py

Il demande le nom de l'établissement, les infirmières (nom, horaires, numéro de poste) et
crée un compte « administration ». Puis il génère un code personnel aléatoire pour chaque
compte et l'AFFICHE UNE SEULE FOIS : notez-les et remettez chacun à son titulaire.
Rien ne sort de la machine ; aucune connexion à Internet n'est nécessaire.
Lancé par deploy/install.sh quand config.json n'existe pas encore (peut aussi se relancer
seul : il demande confirmation avant de remplacer un config.json existant).
"""
import json
import os
import re
import subprocess
import sys
import unicodedata

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(BASE, "config.json")
TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:h]([0-5]\d)$")


def ask(question, default=""):
    try:
        answer = input(f"{question}{f' [{default}]' if default else ''} : ").strip()
    except EOFError:
        answer = ""
    return answer or default


def ask_time(question, default):
    while True:
        value = ask(question, default)
        m = TIME_RE.match(value)
        if m:
            return "%02d:%s" % (int(m.group(1)), m.group(2))
        print("   Format attendu : 08:00 (heures:minutes).")
        if not sys.stdin.isatty():
            return default


def slug(name, taken):
    """Identifiant technique : dernier mot du nom, sans accents ni majuscules (« Mme Dupont » -> dupont)."""
    words = re.sub(r"\b(mme|mlle|m|mr|dr)\b\.?", "", name, flags=re.I).split() or ["infirmiere"]
    base = unicodedata.normalize("NFKD", words[-1]).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]", "", base) or "infirmiere"
    ident, n = base, 2
    while ident in taken:
        ident, n = f"{base}{n}", n + 1
    return ident


def main():
    print("== Configuration de l'affichage de présence des infirmeries ==\n")
    if os.path.exists(CONFIG) and ask("config.json existe déjà. Le REMPLACER (o/N)", "n").lower() not in ("o", "oui"):
        sys.exit("Rien n'a été modifié.")

    school = ask("Nom de l'établissement", "Lycée Exemple")
    title = ask("Titre affiché en haut de l'écran", "Cabinet d'infirmières")
    count = 0
    while not 1 <= count <= 4:
        raw = ask("Nombre d'infirmières (1 à 4)", "2")
        count = int(raw) if raw.isdigit() else 0
        if not sys.stdin.isatty() and not 1 <= count <= 4:
            count = 2
    nurses = []
    for i in range(1, count + 1):
        print(f"\n-- Infirmière {i} --")
        name = ask("Nom affiché (ex. Mme Dupont)", f"Infirmière {i}")
        start = ask_time("Début de journée habituel", "08:00")
        end = ask_time("Fin de journée habituelle", "17:00")
        phone = ask("Numéro de poste interne (facultatif)", "")
        nurse = {"id": slug(name, {n["id"] for n in nurses}), "name": name, "hours": {"default": [start, end]}}
        if phone:
            nurse["phone"] = phone
        nurses.append(nurse)
    print()
    admin = ask("Créer un compte « administration » (statistiques de toutes, lecture seule) (O/n)", "o").lower() in ("o", "oui")

    cfg = {"school": school, "title": title, "logo": "branding/logo.png", "port": 8080,
           "minEventSeconds": 10, "nurses": nurses}
    if admin:
        cfg["admins"] = [{"id": "administration", "name": "Administration"}]
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(CONFIG, 0o600)
    os.makedirs(os.path.join(BASE, "branding"), exist_ok=True)
    print("\nconfig.json écrit. Génération des codes personnels (affichés UNE SEULE FOIS) :\n")
    sys.stdout.flush()
    subprocess.run([sys.executable, os.path.join(BASE, "deploy", "pins.py"), "generate"], check=True)
    print("\nLe logo est facultatif : copiez-le dans branding/logo.png (voir branding/README.md).")
    print("Horaires, noms, postes : modifiables à tout moment dans config.json.")


if __name__ == "__main__":
    main()
