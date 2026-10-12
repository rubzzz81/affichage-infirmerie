#!/usr/bin/env python3
"""Gestion des codes d'accès (infirmières et administration). À lancer sur le Pi :

  python3 deploy/pins.py list                       comptes et état des codes (jamais les codes)
  python3 deploy/pins.py set nurse:broux            définir / changer un code (saisie masquée)
  python3 deploy/pins.py set admin:direction
  python3 deploy/pins.py generate                   codes aléatoires pour les comptes sans code
  python3 deploy/pins.py generate --all             ... pour tous les comptes (remplace les codes)
  python3 deploy/pins.py add-admin direction "Direction"   créer un compte administration
  python3 deploy/pins.py remove-admin direction

Les codes sont stockés hachés (scrypt) dans config.json, jamais en clair. Le serveur prend
les changements en compte immédiatement (sans redémarrage) et ferme les sessions ouvertes
du compte modifié.
"""
import getpass
import json
import os
import secrets
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from auth import MIN_PIN_LENGTH, hash_pin  # noqa: E402

CONFIG = os.path.join(BASE, "config.json")


def load():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def save(cfg):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)                      # le fichier ne doit pas être lisible par les autres comptes
    os.replace(tmp, CONFIG)


def accounts(cfg):
    for role, key in (("nurse", "nurses"), ("admin", "admins")):
        for a in cfg.get(key, []):
            yield f"{role}:{a['id']}", a


def find(cfg, key):
    for k, a in accounts(cfg):
        if k == key:
            return a
    sys.exit(f"Compte inconnu : {key}. Comptes existants : " + ", ".join(k for k, _ in accounts(cfg)))


def weak(pin, ident):
    """Refuse les codes évidents (la longueur minimale est aussi vérifiée)."""
    if len(pin) < MIN_PIN_LENGTH:
        return f"au moins {MIN_PIN_LENGTH} caractères"
    if len(set(pin)) == 1:
        return "un seul caractère répété"
    if pin.isdigit() and (pin in "01234567890123456789" or pin in "98765432109876543210"):
        return "suite de chiffres"
    if pin.lower() in (ident.lower(), "password", "motdepasse", "infirmerie", "azerty", "azerty123"):
        return "trop facile à deviner"
    return None


def set_pin(a, pin):
    a["pinHash"] = hash_pin(pin)
    a.pop("pin", None)


def cmd_list(cfg):
    print(f"{'Compte':28} {'Nom':24} Code")
    for k, a in accounts(cfg):
        state = "défini (haché)" if a.get("pinHash") else ("EN CLAIR dans le fichier !" if a.get("pin") else "non défini")
        print(f"{k:28} {a.get('name', ''):24} {state}")


def cmd_set(cfg, key):
    a = find(cfg, key)
    print(f"Nouveau code pour {key} ({a.get('name', '')}) : {MIN_PIN_LENGTH} caractères minimum.")
    pin = getpass.getpass("Code : ")
    problem = weak(pin, a["id"])
    if problem:
        sys.exit(f"Code refusé : {problem}.")
    if getpass.getpass("Confirmer : ") != pin:
        sys.exit("Les deux saisies sont différentes : rien n'a été modifié.")
    set_pin(a, pin)
    save(cfg)
    print("Code enregistré. Les sessions ouvertes de ce compte sont fermées.")


def cmd_generate(cfg, everyone):
    out = []
    for k, a in accounts(cfg):
        if everyone or not (a.get("pinHash") or a.get("pin")):
            pin = "".join(secrets.choice("0123456789") for _ in range(MIN_PIN_LENGTH))
            while weak(pin, a["id"]):
                pin = "".join(secrets.choice("0123456789") for _ in range(MIN_PIN_LENGTH))
            set_pin(a, pin)
            out.append((k, a.get("name", ""), pin))
    if not out:
        return print("Aucun compte à traiter (tous ont déjà un code ; utilisez --all pour les remplacer).")
    save(cfg)
    print("Codes générés. NOTEZ-LES MAINTENANT : ils ne sont pas stockés en clair et ne seront plus affichés.\n")
    for k, name, pin in out:
        print(f"  {k:28} {name:24} {pin}")
    print("\nRemettez chaque code à son titulaire en main propre, puis effacez ce terminal (commande : clear).")


def cmd_add_admin(cfg, ident, name):
    if not ident.replace("-", "").replace("_", "").isalnum():
        sys.exit("Identifiant invalide : lettres, chiffres, - et _ uniquement.")
    if any(a["id"] == ident for a in cfg.setdefault("admins", [])):
        sys.exit("Ce compte administration existe déjà.")
    cfg["admins"].append({"id": ident, "name": name})
    save(cfg)
    print(f"Compte admin:{ident} créé, sans code. Définissez-le : python3 deploy/pins.py set admin:{ident}")


def cmd_remove_admin(cfg, ident):
    before = len(cfg.get("admins", []))
    cfg["admins"] = [a for a in cfg.get("admins", []) if a["id"] != ident]
    if len(cfg["admins"]) == before:
        sys.exit("Compte administration inconnu.")
    save(cfg)
    print(f"Compte admin:{ident} supprimé (ses sessions sont fermées).")


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        return print(__doc__)
    cfg, cmd = load(), argv[0]
    if cmd == "list":
        cmd_list(cfg)
    elif cmd == "set" and len(argv) == 2:
        cmd_set(cfg, argv[1])
    elif cmd == "generate":
        cmd_generate(cfg, "--all" in argv)
    elif cmd == "add-admin" and len(argv) == 3:
        cmd_add_admin(cfg, argv[1], argv[2])
    elif cmd == "remove-admin" and len(argv) == 2:
        cmd_remove_admin(cfg, argv[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
