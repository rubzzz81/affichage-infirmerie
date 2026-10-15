#!/bin/sh
# Installation sur le Raspberry Pi (sans sudo). À lancer depuis le dossier de l'application :
#   sh deploy/install.sh
set -e
APP="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP"

# 0. Prérequis (présents par défaut sur Raspberry Pi OS avec bureau)
for cmd in python3 curl; do
  command -v $cmd >/dev/null 2>&1 || { echo "Il manque '$cmd' : sudo apt install $cmd"; exit 1; }
done
command -v chromium >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1 \
  || echo "Attention : Chromium est introuvable (l'écran du couloir en a besoin) : sudo apt install chromium"

# 0b. Configuration de l'établissement : assistant interactif si config.json n'existe pas encore
if [ ! -f config.json ]; then
  python3 deploy/setup.py || { echo "Configuration interrompue : relancez sh deploy/install.sh"; exit 1; }
fi
python3 -c "import json; json.load(open('config.json', encoding='utf-8'))" \
  || { echo "config.json n'est pas un JSON valide"; exit 1; }
mkdir -p branding

# 1. Serveur : service systemd utilisateur, redémarré automatiquement s'il plante
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/cabinet.service" <<EOF
[Unit]
Description=Affichage presence infirmerie

[Service]
ExecStart=/usr/bin/python3 $APP/server.py
WorkingDirectory=$APP
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable cabinet.service
systemctl --user restart cabinet.service

# 2. Kiosque : Chromium plein écran au démarrage du bureau (labwc)
cat > "$APP/deploy/kiosk.sh" <<'EOF'
#!/bin/sh
# Attend le serveur puis ouvre l'affichage en plein écran. Relancé s'il se ferme.
APP="$(cd "$(dirname "$0")/.." && pwd)"
PORT=$(python3 -c "import json; print(json.load(open('$APP/config.json', encoding='utf-8')).get('port', 8080))")
until curl -fs "http://localhost:$PORT/api/state" >/dev/null; do sleep 1; done
while true; do
  chromium --kiosk --incognito --noerrdialogs --disable-infobars --no-first-run \
    --disable-session-crashed-bubble --disable-translate --password-store=basic \
    --overscroll-history-navigation=0 --disable-pinch --ozone-platform=wayland \
    "http://localhost:$PORT/"
  sleep 2
done
EOF
chmod +x "$APP/deploy/kiosk.sh"

mkdir -p "$HOME/.config/labwc"
cat > "$HOME/.config/labwc/autostart" <<EOF
/usr/bin/kanshi &
$APP/deploy/kiosk.sh &
EOF
PORT=$(python3 -c "import json; print(json.load(open('config.json', encoding='utf-8')).get('port', 8080))")
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Installation terminée. Redémarrez le Pi pour lancer l'écran : sudo reboot"
echo
echo "  Panneau des infirmières (téléphone, PC) : http://${IP:-<adresse-du-Pi>}:$PORT/admin"
echo "  Statistiques (administration)           : http://${IP:-<adresse-du-Pi>}:$PORT/stats"
echo "  Écran du couloir                        : http://${IP:-<adresse-du-Pi>}:$PORT/"
echo "  (HTTPS, noms et accès depuis Internet sont facultatifs : voir docs/SECURITE.md)"
