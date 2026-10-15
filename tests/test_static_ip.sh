#!/bin/sh
# Teste deploy/set-static-ip.sh SANS toucher au réseau : de faux « nmcli », « ping » et « sleep »
# remplacent les vrais. À lancer depuis le dossier de l'application :  sh tests/test_static_ip.sh
APP="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/home"
cat > "$T/bin/nmcli" <<'EOF'
#!/bin/sh
echo "nmcli $*" >> "$STUBLOG"
case "$*" in
  *"-t -f NAME,DEVICE connection show --active"*) echo "Wired connection 1:eth0";;
  *"-g ipv4.addresses connection show"*) echo "10.0.0.40/8";;
  *"-g ipv4.gateway connection show"*) echo "192.168.1.1";;
  *"-g ipv4.dns connection show"*) echo "8.8.8.8";;
  *"-g ipv4.method connection show"*) echo "manual";;
esac
EOF
cat > "$T/bin/ping" <<'EOF'
#!/bin/sh
[ -f "$STUBDIR/ping_ok" ]
EOF
printf '#!/bin/sh\nexit 0\n' > "$T/bin/sleep"
chmod +x "$T/bin/"*
export STUBDIR="$T" STUBLOG="$T/calls.log" CABINET_IP_NOROOT=1 CABINET_IP_HOME="$T/home" PATH="$T/bin:$PATH"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "OK     $1"; }
ko()  { FAIL=$((FAIL+1)); echo "ÉCHEC  $1"; }
reset() { : > "$STUBLOG"; rm -f "$T/home/cabinet-ip.log" "$T/home/cabinet-ip-backup.txt" "$T/ping_ok" /tmp/cabinet-ip-ok; }
waitlog() { for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do grep -q "RÉSULTAT" "$T/home/cabinet-ip.log" 2>/dev/null && return 0; /bin/sleep 0.2; done; return 1; }
run() { sh "$APP/deploy/set-static-ip.sh" "$@" 2>&1; }

# 1. arguments invalides : refusés avant toute modification
reset; out="$(run 192.168.1.50 192.168.1.1)"
echo "$out" | grep -q "doit porter son masque" && ok "adresse sans masque : refusée" || ko "adresse sans masque : $out"
grep -q "modify" "$STUBLOG" && ko "adresse sans masque : nmcli modify appelé !" || ok "adresse sans masque : rien n'a été modifié"
reset; out="$(run 192.168.1.50/24 10.0.0.1)"
echo "$out" | grep -q "n'est PAS dans le sous-réseau" && ok "passerelle hors du sous-réseau : refusée avec explication" || ko "passerelle hors sous-réseau : $out"
reset; out="$(run 192.168.1.50/24 192.168.1.50)"
echo "$out" | grep -q "ne peut pas être l'adresse du Pi" && ok "passerelle = adresse du Pi : refusée" || ko "passerelle = Pi : $out"
reset; out="$(run 999.1.1.1/24 192.168.1.1)"
echo "$out" | grep -q "invalide" && ok "adresse invalide : refusée" || ko "adresse invalide : $out"
reset; out="$(run)"
echo "$out" | grep -q "Arguments" && ok "sans argument : affiche l'aide" || ko "aide : $out"

# 2. mode à blanc : n'applique rien
reset; out="$(run 192.168.1.50/24 192.168.1.1 --dry-run)"
echo "$out" | grep -q "MODE À BLANC" && ok "--dry-run : annonce qu'il ne modifie rien" || ko "dry-run : $out"
grep -q "modify\|connection up" "$STUBLOG" && ko "--dry-run : a modifié le réseau !" || ok "--dry-run : aucune commande de modification exécutée"
[ ! -f "$T/home/cabinet-ip-backup.txt" ] && ok "--dry-run : aucune sauvegarde ni journal écrits" || ko "dry-run a écrit des fichiers"

# 3. succès : la passerelle répond -> la nouvelle configuration est conservée
reset; touch "$T/ping_ok"; out="$(run 192.168.1.50/24 192.168.1.1)"; waitlog
grep -q "RÉSULTAT : OK" "$T/home/cabinet-ip.log" && ok "passerelle joignable : résultat OK" || ko "succès : $(cat "$T/home/cabinet-ip.log" 2>&1)"
grep -q "modify .*192.168.1.50/24" "$STUBLOG" && ok "la nouvelle adresse a été appliquée" || ko "nouvelle adresse non appliquée"
[ "$(grep -c "modify" "$STUBLOG")" = "1" ] && ok "succès : une seule modification (pas de retour arrière)" || ko "succès : $(grep -c modify "$STUBLOG") modifications"
[ -f "$T/home/cabinet-ip-backup.txt" ] && grep -q "adresse=10.0.0.40/8" "$T/home/cabinet-ip-backup.txt" && ok "l'ancienne configuration est sauvegardée (survit au redémarrage)" || ko "sauvegarde absente"
grep -q "sudo nmcli connection modify" "$T/home/cabinet-ip-backup.txt" && ok "la sauvegarde contient la commande de retour manuel" || ko "commande de retour absente"

# 4. échec : la passerelle ne répond pas -> retour automatique à l'ancienne configuration
reset; out="$(run 192.168.1.50/24 192.168.1.1)"; waitlog
grep -q "RÉSULTAT : ÉCHEC" "$T/home/cabinet-ip.log" && ok "passerelle muette : résultat ÉCHEC" || ko "échec : $(cat "$T/home/cabinet-ip.log" 2>&1)"
[ "$(grep -c "modify" "$STUBLOG")" = "2" ] && ok "échec : deuxième modification = restauration" || ko "échec : $(grep -c modify "$STUBLOG") modifications"
grep "modify" "$STUBLOG" | tail -1 | grep -q "10.0.0.40/8" && ok "la restauration remet 10.0.0.40/8" || ko "restauration incorrecte : $(grep modify "$STUBLOG" | tail -1)"

# 5. --confirm : exige un « touch » humain
reset; touch "$T/ping_ok"; out="$(run 192.168.1.50/24 192.168.1.1 --confirm)"; waitlog
grep -q "RÉSULTAT : ÉCHEC" "$T/home/cabinet-ip.log" && ok "--confirm sans confirmation : retour arrière" || ko "confirm sans touch : $(cat "$T/home/cabinet-ip.log")"
# la partie interactive efface un ancien fichier de confirmation au départ : on lance donc directement le travail
reset; touch "$T/ping_ok" /tmp/cabinet-ip-ok; out="$(run 192.168.1.50/24 192.168.1.1 --worker --confirm)"
grep -q "RÉSULTAT : OK" "$T/home/cabinet-ip.log" && ok "--confirm avec confirmation : conservé" || ko "confirm avec touch : $(cat "$T/home/cabinet-ip.log")"
reset

echo; echo "Résultat : $PASS réussis, $FAIL échecs"
[ "$FAIL" = 0 ]
