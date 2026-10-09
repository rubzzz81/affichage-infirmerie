#!/bin/sh
# Installation sur le Raspberry Pi (sans sudo). À lancer depuis le dossier de l'application :
#   sh deploy/install.sh
set -e
APP="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP"

# 0. Configuration de l'établissement : créée à partir du modèle si elle n'existe pas
if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo ">> config.json créé à partir du modèle : éditez-le (nom de l'établissement, infirmières, logo)"
  echo "   puis relancez : sh deploy/install.sh"
  exit 0
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
echo "Installation terminée. Redémarrez le Pi : sudo reboot"
