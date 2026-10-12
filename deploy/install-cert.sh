#!/bin/sh
# Installe un VRAI certificat (Let's Encrypt, autorité du lycée...) à la place du certificat local.
# Sans sudo. Le serveur le prend en compte tout seul en moins de 15 secondes (pas de redémarrage).
#
#   sh deploy/install-cert.sh chaine-complete.pem cle-privee.pem
#
# chaine-complete.pem : le certificat suivi des certificats intermédiaires (« fullchain »)
# cle-privee.pem      : la clé privée correspondante
set -e
APP="$(cd "$(dirname "$0")/.." && pwd)"
CERT="$1"; KEY="$2"
[ -f "$CERT" ] && [ -f "$KEY" ] || { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

# La clé correspond-elle au certificat ?
A="$(openssl x509 -in "$CERT" -noout -pubkey | openssl sha256)"
B="$(openssl pkey -in "$KEY" -pubout | openssl sha256)"
[ "$A" = "$B" ] || { echo "La clé privée ne correspond pas à ce certificat : rien n'a été modifié."; exit 1; }
openssl x509 -in "$CERT" -noout -checkend 86400 >/dev/null || { echo "Ce certificat est expiré ou expire dans moins d'un jour : rien n'a été modifié."; exit 1; }

mkdir -p "$APP/certs" && chmod 700 "$APP/certs"
cp "$APP/certs/server.crt" "$APP/certs/server.crt.avant" 2>/dev/null || true
cp "$CERT" "$APP/certs/server.crt.new" && cp "$KEY" "$APP/certs/server.key.new"
chmod 644 "$APP/certs/server.crt.new"; chmod 600 "$APP/certs/server.key.new"
mv "$APP/certs/server.key.new" "$APP/certs/server.key"      # la clé d'abord, puis le certificat
mv "$APP/certs/server.crt.new" "$APP/certs/server.crt"

echo "Certificat installé :"
openssl x509 -in "$APP/certs/server.crt" -noout -subject -issuer -enddate | sed 's/^/  /'
echo "Noms couverts :"
openssl x509 -in "$APP/certs/server.crt" -noout -ext subjectAltName 2>/dev/null | tail -n +2 | sed 's/^ */  /'
echo
echo "Pris en compte automatiquement dans les 15 secondes (sans redémarrage)."
echo "Ne relancez PAS deploy/setup-https.sh : il remplacerait ce certificat par le certificat local."
