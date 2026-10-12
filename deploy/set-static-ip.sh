#!/bin/sh
# Change l'adresse IP fixe du Pi AVEC retour arrière automatique. Demande les droits administrateur.
#
#   sh deploy/set-static-ip.sh 10.255.5.223/24 10.255.5.202 [serveur-DNS]
#
# Arguments : adresse/masque (notation CIDR : /24 = 255.255.255.0, /8 = 255.0.0.0), passerelle,
# et facultativement le serveur DNS (8.8.8.8 par défaut).
#
# Sécurité : la nouvelle adresse est appliquée, puis le script attend 120 secondes que vous
# confirmiez que tout fonctionne en tapant, sur la NOUVELLE adresse :   touch /tmp/cabinet-ip-ok
# Sans confirmation, l'ancienne configuration est RESTAURÉE toute seule : une faute de frappe
# dans l'adresse ou la passerelle ne vous enferme donc pas hors du Pi.
#
# À lancer de préférence devant le Pi (clavier + écran) : la connexion SSH ou VNC est coupée
# au moment du changement.
[ "$(id -u)" = 0 ] || exec sudo sh "$0" "$@"
set -e
ADDR="$1"; GW="$2"; DNS="${3:-8.8.8.8}"
if [ -z "$ADDR" ] || [ -z "$GW" ]; then sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 1; fi
case "$ADDR" in */*) ;; *) echo "L'adresse doit porter son masque, par exemple 10.255.5.223/24"; exit 1;; esac

CON="$(nmcli -t -f NAME,DEVICE connection show --active | grep ':eth0$' | cut -d: -f1 | head -1)"
[ -n "$CON" ] || { echo "Connexion filaire (eth0) introuvable"; exit 1; }
OLD_ADDR="$(nmcli -g ipv4.addresses connection show "$CON")"
OLD_GW="$(nmcli -g ipv4.gateway connection show "$CON")"
OLD_DNS="$(nmcli -g ipv4.dns connection show "$CON")"
OLD_METHOD="$(nmcli -g ipv4.method connection show "$CON")"
echo "Connexion : $CON"
echo "Ancienne configuration : $OLD_ADDR via $OLD_GW (DNS $OLD_DNS)"
echo "Nouvelle configuration : $ADDR via $GW (DNS $DNS)"

rm -f /tmp/cabinet-ip-ok
# Retour arrière armé AVANT le changement, dans un processus indépendant
nohup sh -c "
  sleep 120
  if [ ! -f /tmp/cabinet-ip-ok ]; then
    nmcli connection modify '$CON' ipv4.method '$OLD_METHOD' ipv4.addresses '$OLD_ADDR' ipv4.gateway '$OLD_GW' ipv4.dns '$OLD_DNS'
    nmcli connection up '$CON'
    echo 'Retour arrière effectué : pas de confirmation reçue' > /tmp/cabinet-ip-rollback.txt
  fi
" >/dev/null 2>&1 &

nmcli connection modify "$CON" ipv4.method manual ipv4.addresses "$ADDR" ipv4.gateway "$GW" ipv4.dns "$DNS"
nmcli connection up "$CON" >/dev/null
echo
echo "Nouvelle adresse appliquée."
echo "Dans les 120 secondes, depuis la NOUVELLE adresse, confirmez avec :   touch /tmp/cabinet-ip-ok"
echo "Sinon l'ancienne configuration revient automatiquement."
