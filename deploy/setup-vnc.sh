#!/bin/sh
# Accès à distance (VNC) au bureau du Pi, sans sudo. À lancer sur le Pi :
#   sh deploy/setup-vnc.sh
# Utilise wayvnc (installé par défaut sur Raspberry Pi OS avec bureau) en service utilisateur.
# Connexion chiffrée (TLS) + identifiant/mot de passe. Relançable sans risque : les fichiers
# existants (certificat, mot de passe) ne sont jamais écrasés.
set -e
command -v wayvnc >/dev/null || { echo "wayvnc n'est pas installé (sudo apt install wayvnc)"; exit 1; }

D="$HOME/.config/wayvnc"
IP="$(hostname -I | awk '{print $1}')"
mkdir -p "$D" && chmod 700 "$D" && cd "$D"

# Certificat TLS auto-signé (10 ans) et clé RSA pour les clients RealVNC
[ -f tls_key.pem ] || openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
  -keyout tls_key.pem -out tls_cert.pem -subj "/CN=$(hostname)" \
  -addext "subjectAltName=DNS:$(hostname),DNS:$(hostname).local,IP:$IP" 2>/dev/null
[ -f rsa_key.pem ] || { ssh-keygen -m pem -t rsa -b 2048 -N "" -f rsa_key.pem -q; rm -f rsa_key.pem.pub; }

# Configuration : identifiant "vnc" et mot de passe aléatoire (lisible dans ce fichier)
if [ ! -f config ]; then
  PW="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-16)"
  cat > config <<CFG
address=0.0.0.0
port=5900
enable_auth=true
username=vnc
password=$PW
rsa_private_key_file=$D/rsa_key.pem
private_key_file=$D/tls_key.pem
certificate_file=$D/tls_cert.pem
CFG
fi
chmod 600 config tls_key.pem tls_cert.pem rsa_key.pem

# Service utilisateur : démarre avec la session du bureau, relancé s'il s'arrête
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/wayvnc.service" <<UNIT
[Unit]
Description=Bureau a distance VNC (wayvnc)

[Service]
Environment=WAYLAND_DISPLAY=wayland-0
ExecStart=/usr/bin/wayvnc -C %h/.config/wayvnc/config
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable wayvnc.service
systemctl --user restart wayvnc.service
sleep 2
systemctl --user is-active wayvnc.service
echo "VNC prêt : $IP:5900, utilisateur 'vnc'. Le mot de passe est dans $D/config (ligne password=)."
