# Dépannage

Classé par **symptôme** : repérez ce que vous voyez, puis suivez la cause probable et la
solution. Les commandes `bash` se tapent **sur le Pi** (en SSH ou au clavier) ; les adresses sont
des exemples (`192.168.1.50`), remplacez-les par les vôtres.

Avant tout, trois commandes qui répondent à la moitié des questions :

```bash
systemctl --user status cabinet.service      # le serveur tourne-t-il ?
curl -s localhost:8080/api/state             # répond-il ? (JSON avec les infirmières)
python3 deploy/pins.py list                  # quels comptes ont un code ?
```

Pour le fonctionnement courant (sauvegardes, arrivée d'une infirmière, mises à jour), voir
[EXPLOITATION.md](EXPLOITATION.md).

## Sommaire

1. [L'écran du couloir](#1-lécran-du-couloir)
2. [Le panneau des infirmières (téléphone, PC)](#2-le-panneau-des-infirmières-téléphone-pc)
3. [Codes, connexion et blocages](#3-codes-connexion-et-blocages)
4. [Réseau, adresse IP et noms](#4-réseau-adresse-ip-et-noms)
5. [HTTPS et certificats](#5-https-et-certificats)
6. [Statistiques](#6-statistiques)
7. [Préparer et joindre le Pi (carte SD, SSH, VNC)](#7-préparer-et-joindre-le-pi-carte-sd-ssh-vnc)
8. [Accès depuis Internet (option)](#8-accès-depuis-internet-option)

---

## 1. L'écran du couloir

| Symptôme | Cause probable | Solution |
|---|---|---|
| Écran **noir** au démarrage | Le bureau n'a pas lancé le kiosque, ou l'écran est en veille | Voir « Écran en veille » ci-dessous ; sinon lancer `~/cabinet/deploy/kiosk.sh` depuis une session du bureau |
| Écran **noir après un moment** | Extinction automatique de l'écran | `sudo raspi-config` → *Display Options* → *Screen Blanking* → désactiver, puis redémarrer |
| Page « **Impossible de se connecter** » | Le serveur n'est pas démarré | `systemctl --user restart cabinet.service` ; la page se recharge toute seule |
| **Pastille orange** en bas à droite | La page a perdu la connexion au serveur | Rien à faire : elle se reconnecte et se recharge au retour du serveur |
| L'écran affiche « **réservé au réseau** » | L'écran n'est pas considéré comme interne (il passe par une passerelle ou un proxy) | Sur le Pi lui-même, l'accès par `localhost` est toujours autorisé : vérifier que `deploy/kiosk.sh` ouvre bien `http://localhost:<port>/` |
| Les changements **n'apparaissent pas** | Flux temps réel coupé | Recharger (F5) ; vérifier `curl localhost:8080/api/state` : si le JSON est bon, c'est l'écran qui est figé |
| Texte **trop petit** | Plus de 4 infirmières sur un écran | Limiter à 4 colonnes, ou utiliser un second écran |
| Date ou heure **fausses** | Horloge du Pi non synchronisée | `timedatectl` : `System clock synchronized: yes` ; sinon vérifier l'accès au serveur de temps (NTP) |

### Le kiosque se relance en boucle

`deploy/kiosk.sh` relance Chromium s'il se ferme (`sleep 2` entre deux lancements). Si vous voyez
des fenêtres s'ouvrir sans fin :

```bash
pgrep -fc '[d]eploy/kiosk.sh'     # doit afficher 1 : UN seul superviseur
pgrep -fc '[s]erver.py'           # doit afficher 1 : UN seul serveur
```

- **Plus de 1 superviseur** : un `kiosk.sh` a été lancé à la main en plus de celui du bureau. Le
  second Chromium trouve l'instance déjà ouverte et se ferme aussitôt, ce qui fait boucler la
  boucle. Arrêter le surplus (`pkill -f '[d]eploy/kiosk.sh'` puis redémarrer le Pi : `sudo reboot`).
- **Chromium se ferme tout de suite alors qu'il est seul** : le serveur n'a pas répondu à temps.
  Regarder `systemctl --user status cabinet.service` et corriger d'abord le serveur.
- Règle d'or : **après tout essai manuel, vérifier ces deux compteurs.**

### Écran en veille / extinction

`sudo raspi-config` → *Display Options* → *Screen Blanking* → *No*. Redémarrer ensuite. Sur
certaines télés, désactiver aussi la mise en veille automatique dans le menu de la télé.

## 2. Le panneau des infirmières (téléphone, PC)

| Symptôme | Cause probable | Solution |
|---|---|---|
| La page **ne s'ouvre pas** du tout | Le téléphone n'est pas sur le même réseau que le Pi (4G, Wi-Fi invité, autre VLAN) | Se connecter au Wi-Fi de l'établissement qui atteint le Pi ; tester `http://<adresse-du-Pi>:8080/` |
| « **Connexion refusée** » à l'adresse | Mauvais port, ou serveur arrêté | Vérifier le port dans `config.json` (`"port"`) et `systemctl --user status cabinet.service` |
| Le navigateur **redirige vers https** et échoue | Le HTTPS local a été activé (`tls` dans `config.json`) : les pages protégées exigent HTTPS | Utiliser l'adresse `https://…:8443/admin`, ou désactiver le HTTPS (retirer le bloc `tls` et redémarrer) |
| Avertissement « **connexion non sécurisée / certificat** » | Autorité locale non installée sur l'appareil | Voir [§5](#5-https-et-certificats) |
| L'infirmière **ne voit pas son nom** dans la liste | Nom absent de `config.json`, ou liste masquée (accès depuis Internet) | Vérifier `nurses` ; depuis Internet, la liste est volontairement cachée : on saisit son **identifiant** |
| On voit **le panneau d'une autre** | Impossible par conception : droits vérifiés par le serveur | Si un doute : se déconnecter, vider le cookie du site, se reconnecter |
| La page **se déconnecte** toute seule | Durée de session dépassée (12 h infirmière / 2 h administration par défaut) ; ou redémarrage du serveur (sessions en mémoire) | Se reconnecter ; ajuster `sessionHours` dans `config.json` |
| Un bouton **ne fait rien** | Session expirée, ou serveur arrêté | Recharger la page |

## 3. Codes, connexion et blocages

| Symptôme | Cause probable | Solution |
|---|---|---|
| « **Code incorrect** » | Mauvais code, ou compte sans code | `python3 deploy/pins.py list` : « non défini » = aucun code ; sinon `python3 deploy/pins.py set nurse:<identifiant>` |
| **Code oublié** | Les codes sont hachés : impossible de les relire | Définir un nouveau code : `python3 deploy/pins.py set nurse:<identifiant>` (saisie masquée) ou `generate --all` pour tout refaire ; remettre le code en main propre |
| « **Trop de tentatives, réessayez dans X min** » | Blocage de l'adresse (5 échecs) ou du compte (20 échecs) | Attendre, ou débloquer tout de suite : `systemctl --user restart cabinet.service` (les blocages sont en mémoire ; cela déconnecte aussi tout le monde) |
| Plusieurs blocages **sans raison** | Plusieurs personnes derrière une même adresse (partage de connexion) | Les blocages sont comptés par adresse : demander une adresse par poste, ou patienter |
| Un compte **n'arrive jamais à se connecter** | Aucun code défini : un compte sans code ne peut pas se connecter | `python3 deploy/pins.py set <role>:<identifiant>` |
| Code correct mais **refusé depuis Internet** | Second facteur (TOTP) exigé et mal réglé | Voir [§8](#8-accès-depuis-internet-option) |
| Les connexions échouent en **HTTPS seulement** | Date/heure du Pi fausses (certificat « pas encore valide ») | `timedatectl` ; corriger l'heure |

Journal des connexions (sans jamais les codes) :

```bash
tail -n 30 ~/cabinet/auth.log
grep "bloquée" ~/cabinet/auth.log          # blocages : tentative de devinette probable
```

Les identifiants de compte sont du type `nurse:<id>` (infirmière) ou `admin:<id>` (administration),
par exemple `nurse:dupont` ou `admin:administration`.

## 4. Réseau, adresse IP et noms

| Symptôme | Cause probable | Solution |
|---|---|---|
| Le **ping répond mais aucune page ne s'ouvre** | **Masque réseau différent** entre le Pi et le poste (ex. Pi en `/24`, poste en `/8`) : les réponses du Pi prennent un autre chemin (routage asymétrique) et sont bloquées par le pare-feu | Donner au Pi **le même masque** que les postes. Lire celui des postes (`ipconfig` sous Windows) et fixer l'adresse avec `deploy/set-static-ip.sh` (voir ci-dessous) |
| L'**adresse du Pi a changé** | Adresse donnée par DHCP | Fixer l'adresse (ci-dessous) ou réserver une adresse au Pi dans le DHCP |
| `pi-infirmerie.local` **introuvable** | Le poste n'a pas de résolution mDNS (certains réseaux la bloquent) | Utiliser l'adresse IP ; ou demander à l'informatique un vrai nom DNS |
| `admin.pi-infirmerie.local` **introuvable sous Windows** | Windows ne résout que les noms `.local` à **un seul niveau** | Utiliser les noms à un niveau : `pi-infirmerie-admin.local`, `pi-infirmerie-stats.local` |
| Un nom `.local` marche **sur PC mais pas sur Android** | Selon les versions, Android résout mal le mDNS | Utiliser l'adresse IP ou un nom DNS réel |
| La page met **longtemps à charger** puis remplace l'IP par un nom | Redirection vers un nom `.local` introuvable | Corrigé dans cette version (redirections relatives) ; mettre l'application à jour |
| `ssh` : « **Host key verification failed** » | L'adresse a changé : l'empreinte enregistrée ne correspond plus | `ssh-keygen -R <ancienne-adresse>` puis reconnexion (vérifier l'empreinte), ou `-o HostKeyAlias=<ancienne-adresse>` |

### Changer l'adresse IP en sécurité

```bash
# 1. Essai à blanc : affiche ce qui serait fait, ne change rien
sh deploy/set-static-ip.sh 192.168.1.50/24 192.168.1.1 --dry-run
# 2. Pour de bon (de préférence AVEC clavier et écran sur le Pi)
sh deploy/set-static-ip.sh 192.168.1.50/24 192.168.1.1
```

- L'adresse est donnée **avec son masque** (`/24`, `/8`…), et la passerelle doit être **dans le
  même sous-réseau**. Le script refuse sinon.
- Il vérifie que la passerelle répond au ping, change l'adresse, puis **annule tout seul** (retour
  à l'ancienne adresse) si la nouvelle ne fonctionne pas.
- Trace : `~/cabinet-ip.log` ; sauvegarde de l'ancien réglage : `~/cabinet-ip-backup.txt`.
- Si la connexion SSH est coupée par le changement, c'est normal : se reconnecter à la **nouvelle**
  adresse.
- Après changement, le **certificat HTTPS local** ne couvre plus la nouvelle adresse :
  voir [§5](#5-https-et-certificats).

## 5. HTTPS et certificats

| Symptôme | Cause probable | Solution |
|---|---|---|
| « **Votre connexion n'est pas privée** » | Autorité locale non installée sur l'appareil | Ouvrir `https://<adresse>:8443/certificat` (réseau interne) : page d'aide pas à pas ; installer `ca.crt` sur l'appareil |
| Avertissement **après un changement d'adresse** | Le certificat ne contient plus la bonne adresse | `sh deploy/setup-https.sh`, puis `systemctl --user restart cabinet.service` |
| Avertissement **après un renouvellement** | Autorité locale régénérée | Réinstaller `ca.crt` sur les appareils ; ne pas relancer `setup-https.sh` sans nécessité |
| Le navigateur **ne propose plus de continuer** | HSTS : le navigateur a mémorisé « HTTPS obligatoire » pour ce nom | Corriger la cause (certificat) ; en dernier recours, vider les données du site |
| Python / un outil refuse le certificat | Autorité sans `keyUsage` / `basicConstraints` (anciens scripts) | Régénérer avec la version actuelle de `deploy/setup-https.sh` |
| Après `install-cert.sh`, l'**ancien certificat** est servi | Le serveur recharge un certificat renouvelé en ≤ 15 s | Attendre 15 s ; sinon `systemctl --user restart cabinet.service` |
| Ports **80 / 443 déjà pris** | Un autre serveur (nginx…) occupe le port | Garder `8443` ; ne pas utiliser `deploy/use-standard-ports.sh` dans ce cas |

## 6. Statistiques

| Symptôme | Cause probable | Solution |
|---|---|---|
| Chiffres **bizarres** (durées très longues) | Une consultation est restée ouverte (oubli d'appui) | Les statistiques mesurent ce que les infirmières déclarent ; rappeler d'appuyer sur « Présente » en fin de consultation |
| **Aucune donnée** avant une date | Les mesures commencent au déploiement | Normal : pas d'historique antérieur |
| Les changements **très courts** manquent | Les changements de moins de `minEventSeconds` (10 s) sont ignorés | Voulu (appuis par erreur) ; réglable dans `config.json` |
| Une infirmière **disparaît** des statistiques | Elle a été retirée de `config.json` | Ses données restent dans `data.db` ; la remettre dans `nurses` avec **le même `id`** pour les retrouver |
| **Fausses statistiques** de démonstration encore présentes | Ajoutées par `tools/demo-data.py --merge` | `python3 tools/demo-data.py . --remove` (les vraies données ne sont pas touchées) |
| Le **taux d'occupation** semble faux | Définition : temps en consultation ÷ temps de présence (présente + consultation + absence temporaire) | Voir « Comment lire ces chiffres » en bas de la page `/stats` |

## 7. Préparer et joindre le Pi (carte SD, SSH, VNC)

| Symptôme | Cause probable | Solution |
|---|---|---|
| Raspberry Pi Imager : « **Error writing to storage device** » | Carte, lecteur ou disque en cause (carte défectueuse, protégée en écriture, mauvais contact) | Essayer un autre lecteur ou port USB, une autre carte ; effacer la carte (`diskpart` → `clean`, ou *SD Memory Card Formatter*) ; tester la carte (*H2testw*) ; en dernier recours remplacer la carte |
| PuTTY : « **Couldn't agree a key exchange algorithm** » | PuTTY trop ancien pour l'OpenSSH du Pi | Mettre PuTTY à jour (ou utiliser `ssh` de PowerShell) |
| `ssh` : « **Connection timed out** » | Mauvaise adresse, Pi éteint, ou SSH non activé | Vérifier l'adresse (écran du Pi, box, switch) ; SSH s'active dans Pi Imager (personnalisation) ou `sudo raspi-config` |
| `ssh` : « **Permission denied** » | Mauvais utilisateur ou mot de passe | Utiliser l'utilisateur défini dans Pi Imager |
| VNC (TigerVNC) : « **connection refused** » sur le port `59000` | `hôte:5900` est lu comme un numéro d'écran (5900 + 5900) | Saisir l'hôte **sans port** (`192.168.1.50`) ou `192.168.1.50::5900` |
| VNC : écran noir ou refus | `wayvnc` arrêté | `systemctl --user status wayvnc.service` ; le mot de passe est dans `~/.config/wayvnc/config` |
| Trouver l'adresse d'un Pi **sans écran** | — | Écran du Pi (`hostname -I`), liste des baux de la box/du DHCP, ou `ping pi-infirmerie.local` |

Le [guide pas à pas](GUIDE.md) détaille ces étapes avec des captures.

## 8. Accès depuis Internet (option)

Ne concerne que ceux qui ont suivi [INTERNET.md](INTERNET.md).

| Symptôme | Cause probable | Solution |
|---|---|---|
| Rien ne répond depuis l'extérieur | Redirection de port absente ou filtrée | Vérifier avec le service informatique une **seule** redirection vers le port HTTPS du Pi ; tester `python3 tools/check-exposure.py <adresse> <port>` **depuis un autre réseau** |
| Un autre site répond sur 80 / 443 | Ces ports sont servis par un autre serveur | Utiliser le port 8443 |
| Connexion refusée alors que le code est bon | Second facteur exigé (`"totp": "external"`) et non inscrit | `python3 deploy/pins.py totp nurse:<id>`, ou régler `"totp": "never"` (déconseillé : le code seul protège alors le compte) |
| Code de l'application **toujours refusé** | Horloge du téléphone ou du Pi décalée | Régler l'heure automatiquement ; un code n'est accepté **qu'une fois** (anti-rejeu) : attendre le code suivant |
| Un visiteur externe voit la page d'accueil mais **pas l'écran** | Voulu : rien n'est lisible sans connexion | `publicDisplay: "everyone"` pour l'ouvrir (déconseillé) |
| Les adresses **internes sont vues comme externes** | Un pare-feu ou proxy fait de la traduction d'adresse source | Le Pi voit alors l'adresse de la passerelle, toujours traitée comme externe : demander **la conservation de l'adresse source** |
| Un verrou **externe** bloque une infirmière au lycée | Impossible : les compteurs interne et externe sont séparés | Vérifier `auth.log` : la tentative vient-elle bien d'une adresse interne ? |

## Rien de tout cela ne correspond ?

1. Relever l'heure exacte du problème, le message affiché, et ce qui a changé juste avant (mise à jour, changement d'adresse, coupure).
2. Lire `tail -n 50 ~/cabinet/auth.log` et `systemctl --user status cabinet.service`.
3. Lancer les suites de tests sur une **copie** (jamais dans le dossier de production) : `python3 tests/test_security.py` ; elles vérifient l'essentiel du comportement du serveur.
