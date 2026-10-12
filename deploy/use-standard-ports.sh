#!/bin/sh
# Ports standards : le site devient joignable par https://pi-infirmerie.local (sans ":8443"),
# et http://pi-infirmerie.local redirige vers lui. À lancer sur le Pi par un compte "sudo" :
#   sh deploy/use-standard-ports.sh
#
# Pourquoi sudo : sous Linux, les ports inférieurs à 1024 sont réservés. Le script autorise
# les ports à partir de 80 pour tous les comptes (réglage système) puis passe l'application
# sur les ports 80 (HTTP) et 443 (HTTPS). Pour revenir en arrière : sh deploy/use-standard-ports.sh --undo
set -e
APP="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP"
[ -f config.json ] || { echo "config.json introuvable"; exit 1; }

if [ "$1" = "--undo" ]; then HTTP=8080; HTTPS=8443; LIMIT=1024; else HTTP=80; HTTPS=443; LIMIT=80; fi

echo "Réglage système (mot de passe sudo demandé) : ports non privilégiés à partir de $LIMIT"
echo "net.ipv4.ip_unprivileged_port_start=$LIMIT" | sudo tee /etc/sysctl.d/50-cabinet-ports.conf >/dev/null
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=$LIMIT >/dev/null

HTTP=$HTTP HTTPS=$HTTPS python3 - <<'PY'
import json, os
p = "config.json"
cfg = json.load(open(p, encoding="utf-8"))
cfg["port"] = int(os.environ["HTTP"])
if "tls" in cfg:
    cfg["tls"]["port"] = int(os.environ["HTTPS"])
tmp = p + ".tmp"
json.dump(cfg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
os.chmod(tmp, 0o600); os.replace(tmp, p)
PY

systemctl --user restart cabinet.service
sleep 2
systemctl --user is-active cabinet.service
echo "Le serveur écoute maintenant sur les ports $HTTP (HTTP) et $HTTPS (HTTPS)."
echo "Le kiosque lit le port dans config.json : redémarrez le Pi (sudo reboot) pour qu'il le prenne en compte."
