# Installation rapide (réseau local, sans Internet)

Objectif : un écran dans le couloir et un panneau sur le téléphone de chaque infirmière, en
**une trentaine de minutes**, **sans nom de domaine, sans certificat, sans ouvrir quoi que ce soit
sur Internet**. Tout reste dans le réseau de l'établissement.

> Besoin de davantage (HTTPS, noms faciles à retenir, accès depuis Internet) ? Ce sont des
> **options** à ajouter plus tard, sans rien refaire : voir la fin de ce document.

## Ce qu'il faut

| Matériel / information | Remarque |
|---|---|
| Un **Raspberry Pi** (3, 4 ou 5) + carte microSD 16 Go + alimentation | Un Pi 4 ou 5 est confortable |
| Un **écran ou une télé** avec une entrée HDMI | Placé dans le couloir |
| Le Pi **relié au réseau** de l'établissement (câble conseillé) | Il doit être joignable depuis les téléphones et PC des infirmières (même réseau ou réseau relié) |
| Les **noms** des infirmières, leurs **horaires** (et leur n° de poste si on veut l'afficher) | Le logo est facultatif |

Aucun abonnement, aucun compte en ligne : l'application est libre (licence MIT) et n'utilise que
Python 3, déjà présent sur le Pi.

## Étape 1 : préparer la carte SD (10 min)

1. Installer **Raspberry Pi Imager** (gratuit) sur un PC.
2. Choisir **Raspberry Pi OS (64-bit)** *avec bureau*, puis la carte SD.
3. Dans les réglages avancés : nom d'hôte `pi-infirmerie`, **activer SSH**, définir un utilisateur
   (par exemple `infirmerie`) et son mot de passe, régler le pays et le fuseau horaire (Europe/Paris).
4. Écrire la carte, la mettre dans le Pi, brancher l'écran et le câble réseau, puis alimenter.

(Le [guide pas à pas](GUIDE.md) détaille ces écrans avec des captures, ainsi que les problèmes courants.)

## Étape 2 : récupérer l'application (2 min)

Depuis un PC du réseau, se connecter au Pi (`ssh infirmerie@pi-infirmerie.local`, ou avec son
adresse IP affichée par la box / le routeur / l'écran du Pi), puis :

```bash
git clone <adresse-du-dépôt> cabinet
cd cabinet
```

(Sans `git` : télécharger l'archive du dépôt, la copier avec `scp` ou une clé USB, la décompresser
dans `/home/infirmerie/cabinet`.)

## Étape 3 : installer (5 min)

```bash
sh deploy/install.sh
```

Le script s'occupe de tout, **sans `sudo`** :

1. il vérifie que Python 3, `curl` et Chromium sont présents ;
2. il lance l'**assistant de configuration** (`deploy/setup.py`) qui pose quelques questions :
   nom de l'établissement, nombre d'infirmières, nom / horaires / numéro de poste de chacune,
   compte « administration » ;
3. il **génère un code personnel** aléatoire pour chaque compte et l'**affiche une seule fois** :
   notez-les, ils ne sont stockés que sous forme hachée et ne pourront pas être relus ;
4. il installe le serveur (redémarré automatiquement s'il plante) et l'écran plein écran au
   démarrage du bureau ;
5. il affiche les adresses à utiliser.

Puis : `sudo reboot`. Au redémarrage, l'écran du couloir affiche les infirmières.

## Étape 4 : utiliser (5 min)

| Qui | Adresse (remplacer par l'adresse affichée par l'installation) | Pour faire quoi |
|---|---|---|
| L'écran du couloir | `http://<adresse-du-Pi>:8080/` | Affichage (automatique) |
| Chaque infirmière | `http://<adresse-du-Pi>:8080/admin` | Choisir son nom, saisir son code, changer d'état en deux touches |
| L'administration | `http://<adresse-du-Pi>:8080/stats` | Statistiques de toutes les infirmières (lecture seule) |

Conseil : **ajouter la page `/admin` à l'écran d'accueil du téléphone** de chaque infirmière.

Donner à chacune **son code en main propre**. Les droits sont vérifiés par le serveur : une infirmière
ne voit et ne modifie que son propre panneau et ses propres statistiques.

## Après l'installation

| Je veux… | Comment |
|---|---|
| Ajouter mon **logo** | Copier l'image dans `branding/logo.png` (voir `branding/README.md`) |
| Changer des **horaires**, un nom, un n° de poste | Éditer `config.json`, puis `systemctl --user restart cabinet.service` |
| **Changer ou refaire un code** | `python3 deploy/pins.py set nurse:<identifiant>` ou `python3 deploy/pins.py generate --all` |
| **Fixer l'adresse du Pi** (recommandé : sinon elle peut changer) | Réserver une adresse au Pi dans la box / le DHCP, ou `sh deploy/set-static-ip.sh` (voir [guide](GUIDE.md)) |
| Mettre un **exemple de statistiques** pour une démonstration | [ADAPTER.md](ADAPTER.md#présenter-la-solution-avec-de-fausses-statistiques) |

## Sécurité sur un réseau local

Sans réglage supplémentaire : chaque personne a **son code**, six chiffres ou plus, stocké haché ;
les essais ratés sont limités puis bloqués temporairement ; l'écran du couloir est lisible depuis
le réseau interne mais **ne permet aucune modification** ; les changements d'état exigent une
session connectée. Les codes circulent en clair sur le réseau interne (HTTP) : c'est acceptable
sur un réseau d'établissement maîtrisé, et le **HTTPS** s'ajoute en une commande si vous voulez
l'éviter (ci-dessous).

## Les options (à faire plus tard, une par une)

| Option | À quoi ça sert | Où |
|---|---|---|
| **HTTPS** local | Chiffrer les codes sur le réseau | `sh deploy/setup-https.sh`, puis [SECURITE.md](SECURITE.md) |
| **Noms** au lieu de l'adresse IP (`pi-infirmerie-admin.local`) | Ne plus taper l'adresse | [SECURITE.md](SECURITE.md) |
| **Fixer l'adresse** du Pi | Qu'elle ne change jamais | [GUIDE.md](GUIDE.md) |
| **Accès à distance** de l'écran (VNC) | Dépanner à distance | `sh deploy/setup-vnc.sh` |
| **Accès depuis Internet** | Consulter le panneau hors établissement | **Déconseillé par défaut**, à n'envisager qu'avec le service informatique : [INTERNET.md](INTERNET.md) |

Aucune de ces options n'est nécessaire au fonctionnement.

## Un problème ?

| Symptôme | Piste |
|---|---|
| L'écran reste noir ou affiche une erreur | Le serveur n'est pas lancé : `systemctl --user status cabinet.service` |
| Le téléphone n'ouvre pas la page | Pas sur le même réseau que le Pi ; vérifier l'adresse (`hostname -I` sur le Pi) |
| « Code incorrect » | Refaire un code : `python3 deploy/pins.py set nurse:<identifiant>` |
| Compte bloqué | Attendre quelques minutes (blocage temporaire) |

Autres cas : [GUIDE.md](GUIDE.md) (annexe « Problèmes courants »).
