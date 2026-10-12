#!/bin/sh
# Active le HTTPS (sans sudo). À lancer sur le Pi, depuis le dossier de l'application :
#   sh deploy/setup-https.sh                       certificat pour le nom et l'IP du Pi
#   sh deploy/setup-https.sh infirmerie.lycee.fr   ... + un nom DNS supplémentaire (autant que voulu)
#
# Crée une petite autorité de certification privée (certs/ca.crt) et un certificat de serveur
# signé par elle (certs/server.crt). Installez ca.crt UNE FOIS sur chaque appareil qui doit
# se connecter (PC de l'administration, téléphones des infirmières) : il n'y aura plus aucun
# avertissement de sécurité dans le navigateur. Les fichiers .key sont secrets.
set -e
APP="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP"
mkdir -p certs && chmod 700 certs && cd certs

HOST="$(hostname)"
SAN="DNS:$HOST,DNS:$HOST.local"
for ip in $(hostname -I); do SAN="$SAN,IP:$ip"; done
for name in "$@"; do SAN="$SAN,DNS:$name"; done

if [ ! -f ca.key ]; then
  openssl genrsa -out ca.key 4096 2>/dev/null
  openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
    -subj "/CN=Cabinet infirmieres - autorite locale ($HOST)" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" -out ca.crt
fi

# Certificat serveur (825 jours : limite acceptée par les navigateurs Apple)
openssl genrsa -out server.key 2048 2>/dev/null
openssl req -new -key server.key -subj "/CN=$HOST" -out server.csr
cat > server.ext <<EXT
subjectAltName=$SAN
basicConstraints=critical,CA:FALSE
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EXT
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 825 -sha256 -extfile server.ext 2>/dev/null
rm -f server.csr server.ext
chmod 600 ca.key server.key
chmod 644 ca.crt server.crt

# Déclare le HTTPS dans config.json (sans toucher au reste)
cd "$APP"
python3 - <<'PY'
import json, os
p = "config.json"
cfg = json.load(open(p, encoding="utf-8"))
cfg.setdefault("tls", {"port": 8443, "cert": "certs/server.crt", "key": "certs/server.key"})
tmp = p + ".tmp"
json.dump(cfg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
os.chmod(tmp, 0o600); os.replace(tmp, p)
PY

echo
echo "Certificat créé pour : $SAN"
echo "Empreinte de l'autorité (à comparer sur les appareils) :"
openssl x509 -in certs/ca.crt -noout -fingerprint -sha256
echo
echo "Redémarrez le serveur : systemctl --user restart cabinet.service"
echo "Puis installez certs/ca.crt sur les appareils (voir docs/SECURITE.md)."
