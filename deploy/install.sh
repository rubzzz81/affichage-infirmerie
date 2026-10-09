#!/bin/sh
# Installation sur le Raspberry Pi (sans sudo). À lancer depuis ~/cabinet : sh deploy/install.sh
set -e
APP="$HOME/cabinet"

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
until curl -fs http://localhost:8080/api/state >/dev/null; do sleep 1; done
while true; do
  chromium --kiosk --incognito --noerrdialogs --disable-infobars --no-first-run \
    --disable-session-crashed-bubble --disable-translate --password-store=basic \
    --overscroll-history-navigation=0 --disable-pinch --ozone-platform=wayland \
    http://localhost:8080/
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
