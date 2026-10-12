#!/bin/sh
# Change l'adresse IP fixe du Pi, vérifie que le réseau fonctionne, et revient en arrière sinon.
# Demande les droits administrateur (mot de passe sudo).
#
#   sh deploy/set-static-ip.sh 10.255.5.223/24 10.255.5.202 [serveur-DNS] [options]
#
# Arguments : adresse/masque (notation CIDR : /24 = 255.255.255.0, /8 = 255.0.0.0), passerelle, et
# facultativement le serveur DNS (8.8.8.8 par défaut).
#
# Options :
#   --dry-run   n'applique rien : affiche seulement ce qui serait fait et contrôle les arguments
#   --confirm   exige en plus une confirmation humaine (touch /tmp/cabinet-ip-ok dans les 120 s)
#
# Comment ça marche
#   1. contrôle les arguments (la passerelle doit être DANS le même sous-réseau que l'adresse) ;
#   2. sauvegarde l'ancienne configuration dans ~/cabinet-ip-backup.txt ;
#   3. applique la nouvelle adresse ;
#   4. vérifie que la PASSERELLE répond (ping) ; si oui, c'est terminé ; sinon, l'ancienne
#      configuration est restaurée automatiquement ;
#   5. résultat écrit dans ~/cabinet-ip.log (il survit au redémarrage).
#
# À lancer de préférence devant le Pi (clavier + écran) : la connexion SSH ou VNC est coupée au
# changement, et votre PC peut ne plus joindre le Pi s'il n'est pas sur le même réseau que la
# nouvelle adresse : c'est normal, ce n'est pas un échec. Le Pi, lui, est joignable de son réseau.
case " $* " in *" --dry-run "*) NEEDROOT=0;; *) NEEDROOT=1;; esac   # le mode à blanc ne demande pas sudo
[ "$CABINET_IP_NOROOT" = "1" ] || [ "$(id -u)" = 0 ] || [ "$NEEDROOT" = 0 ] || exec sudo sh "$0" "$@"

DRY=0; CONFIRM=0; WORKER=0; ARGS=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1;; --confirm) CONFIRM=1;; --worker) WORKER=1;;
    *) ARGS="$ARGS $a";;
  esac
done
set -- $ARGS
ADDR="$1"; GW="$2"; DNS="${3:-8.8.8.8}"
USER_HOME="$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)"; USER_HOME="${USER_HOME:-$HOME}"
USER_HOME="${CABINET_IP_HOME:-$USER_HOME}"          # (réglage réservé aux tests)
LOG="$USER_HOME/cabinet-ip.log"
BACKUP="$USER_HOME/cabinet-ip-backup.txt"
say() { echo "$*"; [ "$DRY" = 1 ] || echo "$(date '+%F %T') $*" >> "$LOG"; }

if [ -z "$ADDR" ] || [ -z "$GW" ]; then sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 1; fi

# --- 1. contrôle des arguments
CHECK="$(ADDR="$ADDR" GW="$GW" python3 - <<'PY'
import ipaddress, os
try:
    itf = ipaddress.ip_interface(os.environ["ADDR"])
    gw = ipaddress.ip_address(os.environ["GW"])
except ValueError as e:
    print("ERREUR adresse ou passerelle invalide : %s" % e); raise SystemExit
if "/" not in os.environ["ADDR"]:
    print("ERREUR l'adresse doit porter son masque, par exemple 10.255.5.223/24")
elif itf.version != 4 or gw.version != 4:
    print("ERREUR seules les adresses IPv4 sont gérées ici")
elif gw not in itf.network:
    print("ERREUR la passerelle %s n'est PAS dans le sous-réseau %s de cette adresse : le Pi ne pourrait pas la joindre. "
          "Avec un masque /24, adresse et passerelle doivent partager les trois premiers nombres ; vérifiez le masque avec le service informatique." % (gw, itf.network))
elif gw == itf.ip:
    print("ERREUR la passerelle ne peut pas être l'adresse du Pi")
else:
    print("OK %s" % itf.network)
PY
)"
case "$CHECK" in OK*) ;; *) echo "$CHECK"; exit 1;; esac
NET="${CHECK#OK }"

CON="$(nmcli -t -f NAME,DEVICE connection show --active | grep ':eth0$' | cut -d: -f1 | head -1)"
[ -n "$CON" ] || { echo "ERREUR connexion filaire (eth0) introuvable"; exit 1; }
OLD_ADDR="$(nmcli -g ipv4.addresses connection show "$CON")"
OLD_GW="$(nmcli -g ipv4.gateway connection show "$CON")"
OLD_DNS="$(nmcli -g ipv4.dns connection show "$CON")"
OLD_METHOD="$(nmcli -g ipv4.method connection show "$CON")"

# --- 2/3. travail réel : exécuté séparé du terminal, car la connexion SSH tombe pendant le changement
if [ "$WORKER" = 1 ]; then
  say "== début : $OLD_ADDR via $OLD_GW  ->  $ADDR via $GW (DNS $DNS)"
  nmcli connection modify "$CON" ipv4.method manual ipv4.addresses "$ADDR" ipv4.gateway "$GW" ipv4.dns "$DNS"
  nmcli connection up "$CON" >/dev/null 2>&1
  GWIP="${GW%%/*}"; OK=0
  # --- 4. la passerelle répond-elle ? (jusqu'à 30 s : le temps que le lien se rétablisse)
  for i in 1 2 3 4 5 6; do
    sleep 5
    if ping -c 2 -W 2 "$GWIP" >/dev/null 2>&1; then OK=1; break; fi
  done
  if [ "$OK" = 1 ] && [ "$CONFIRM" = 1 ]; then
    say "passerelle joignable ; en attente de confirmation (touch /tmp/cabinet-ip-ok) pendant 120 s"
    OK=0
    for i in $(seq 1 24); do [ -f /tmp/cabinet-ip-ok ] && { OK=1; break; }; sleep 5; done
  fi
  if [ "$OK" = 1 ]; then
    INET="non"; ping -c 1 -W 3 1.1.1.1 >/dev/null 2>&1 && INET="oui"
    say "RÉSULTAT : OK - adresse $ADDR appliquée, passerelle $GWIP joignable, sortie Internet : $INET"
    # l'annonce mDNS et le certificat local portent l'ancienne adresse : on relance le service
    if [ -n "$SUDO_USER" ]; then
      UID_N="$(id -u "$SUDO_USER")"
      su "$SUDO_USER" -c "XDG_RUNTIME_DIR=/run/user/$UID_N systemctl --user restart cabinet.service" >/dev/null 2>&1 \
        && say "service de l'application relancé"
    fi
    say "Si le certificat HTTPS est le certificat local : relancer 'sh deploy/setup-https.sh' pour y ajouter la nouvelle adresse."
  else
    say "RÉSULTAT : ÉCHEC - la passerelle $GWIP ne répond pas ; restauration de l'ancienne configuration"
    nmcli connection modify "$CON" ipv4.method "$OLD_METHOD" ipv4.addresses "$OLD_ADDR" ipv4.gateway "$OLD_GW" ipv4.dns "$OLD_DNS"
    nmcli connection up "$CON" >/dev/null 2>&1
    say "ancienne configuration rétablie : $OLD_ADDR via $OLD_GW"
  fi
  exit 0
fi

# --- partie interactive : annonce, sauvegarde, puis lance le travail en arrière-plan
echo "Connexion          : $CON"
echo "Ancienne config    : $OLD_ADDR via $OLD_GW (DNS $OLD_DNS)"
echo "Nouvelle config    : $ADDR via $GW (DNS $DNS)    [sous-réseau $NET]"
if [ "$DRY" = 1 ]; then
  echo
  echo "MODE À BLANC : rien n'a été modifié. Commandes qui seraient exécutées :"
  echo "  nmcli connection modify '$CON' ipv4.method manual ipv4.addresses '$ADDR' ipv4.gateway '$GW' ipv4.dns '$DNS'"
  echo "  nmcli connection up '$CON'"
  echo "  puis ping de la passerelle $GW pendant 30 s ; sinon restauration de $OLD_ADDR via $OLD_GW"
  exit 0
fi
{
  echo "# Sauvegarde du $(date '+%F %T') avant le changement d'adresse"
  echo "# Pour revenir à la main : sudo nmcli connection modify '$CON' ipv4.method $OLD_METHOD ipv4.addresses '$OLD_ADDR' ipv4.gateway '$OLD_GW' ipv4.dns '$OLD_DNS' && sudo nmcli connection up '$CON'"
  echo "connexion=$CON"; echo "adresse=$OLD_ADDR"; echo "passerelle=$OLD_GW"; echo "dns=$OLD_DNS"; echo "methode=$OLD_METHOD"
} > "$BACKUP"
chown "${SUDO_USER:-$USER}" "$BACKUP" 2>/dev/null || true
rm -f /tmp/cabinet-ip-ok
echo
echo "Sauvegarde de l'ancienne configuration : $BACKUP"
echo "Application dans 3 secondes. Le résultat sera écrit dans : $LOG"
echo "(la connexion SSH/VNC va tomber ; sur l'écran du Pi : cat $LOG)"
sleep 3
W=""; [ "$CONFIRM" = 1 ] && W="--confirm"
setsid nohup sh "$0" "$ADDR" "$GW" "$DNS" --worker $W >/dev/null 2>&1 &
exit 0
