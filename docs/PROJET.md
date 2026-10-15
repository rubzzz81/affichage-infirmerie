# Dossier du projet

Ce document résume **tout ce qui a été fait**, **pourquoi** chaque choix a été fait, **dans quel état** se trouve l'installation, et **ce qui reste à faire**.
Il sert de mémoire du projet : à lire avant de reprendre la main ou de passer le relais.

> État décrit : **12 octobre 2026**. Les valeurs d'installation (adresses, ports) sont celles du Lycée Louis Rascol ; pour un autre établissement, voir [ADAPTER.md](ADAPTER.md).

## Sommaire

1. [Le besoin](#1-le-besoin)
2. [Ce qui est livré](#2-ce-qui-est-livré)
3. [Architecture](#3-architecture)
4. [Décisions techniques et leurs raisons](#4-décisions-techniques-et-leurs-raisons)
5. [État actuel de l'installation](#5-état-actuel-de-linstallation)
6. [Chronologie](#6-chronologie)
7. [Incidents rencontrés et leçons](#7-incidents-rencontrés-et-leçons)
8. [Tests](#8-tests)
9. [Ce qui reste à faire](#9-ce-qui-reste-à-faire)
10. [Carte de la documentation](#10-carte-de-la-documentation)

---

## 1. Le besoin

Les cabinets d'infirmières du lycée n'ont aucun moyen simple d'indiquer **si l'infirmière est présente**. Il fallait :

- un **grand écran** dans le couloir, branché sur un Raspberry Pi, qui affiche pour chaque infirmière son **état** (présente, en consultation, absente un instant, absente) et ses **horaires du jour** ;
- que **chaque infirmière change son état elle-même**, en deux gestes, depuis son téléphone ;
- un **suivi d'activité** : élèves reçus, durée moyenne des consultations, taux d'occupation du cabinet, nombre d'usages de chaque état ;
- une solution **gratuite, libre, élégante et simple**, **réutilisable par d'autres lycées** (autre logo, autres noms) ;
- des **accès sécurisés** : l'administration voit les statistiques, chaque infirmière ne pilote que son panneau, et rien n'est modifiable par un élève ;
- un **accès depuis Internet** pour les infirmières qui ne sont pas sur le même réseau que le Pi (par un seul port).

Le schéma d'origine :

![Schéma initial dessiné à la main](img/maquette-initiale.jpg)

## 2. Ce qui est livré

| Domaine | Fonctionnalités |
|---|---|
| **Écran du couloir** | Logo, titre « Cabinet d'infirmières », heure, une carte par infirmière (1 à 4), couleur et texte selon l'état, horaires du jour, barre de séparation, mise à jour **en moins d'une seconde** (flux temps réel), s'adapte à toute taille d'écran |
| **Les 4 états** | Présente (vert), En consultation (bleu), Absente temporairement (orange, avec heure de retour facultative), Absente (rouge) |
| **Panneau des infirmières** | Gros boutons sur téléphone, « Élève suivant », heure de retour en un geste (15 min, 30 min, 1 h, heure précise), horaires du jour modifiables |
| **Automatismes** | Passage en « Absente » à l'heure de fin ; remise à zéro chaque nuit ; fermeture réarmée si les horaires sont allongés |
| **Statistiques** | Journal SQLite ; élèves reçus, durée moyenne, taux d'occupation, usage de chaque état ; par jour, par heure, par jour de semaine ; vue par infirmière ; exports CSV ; impression PDF |
| **Accès** | Comptes personnels (infirmières, administration), codes hachés, sessions, blocage des essais, HTTPS, journal des connexions |
| **Internet** | Un seul port HTTPS ; second facteur obligatoire depuis l'extérieur ; rien de lisible sans connexion ; limites de connexions ; HSTS ; rechargement automatique du certificat |
| **Noms et réseau** | Trois sites (écran public, panneau, statistiques) ; noms `.local` annoncés automatiquement (mDNS) ; changement d'IP sécurisé ; ports 80/443 |
| **Exploitation** | Accès à distance au bureau (VNC chiffré) ; vérificateur d'exposition ; scripts d'installation ; 148 contrôles automatiques |
| **Multi-établissements** | Nom, titre, logo, infirmières, domaine dans `config.json` ; rien d'autre à modifier |

## 3. Architecture

```
                         Internet
                            │
              Pare-feu du lycée (45.15.220.130)
              redirection TCP 8443 ──► Pi, sans NAT source
                            │
 ┌──────────────────────────┴───────────────────────────────────────────┐
 │  Raspberry Pi  « pi-infirmerie »   10.255.5.223/8   passerelle 10.255.5.202
 │                                                                       │
 │   server.py  (Python, bibliothèque standard uniquement)               │
 │     ├─ HTTP  :8080   écran public, état, flux temps réel              │
 │     ├─ HTTPS :8443   panneau, statistiques, connexion (tout protégé)  │
 │     ├─ auth.py       comptes, scrypt, sessions, TOTP, blocages        │
 │     ├─ mdns.py       annonce les noms .local (sans rien installer)    │
 │     ├─ state.json    état courant ;  data.db  journal d'activité      │
 │     └─ auth.log      journal des connexions                           │
 │                                                                       │
 │   Chromium (kiosque) ──► http://localhost:8080/  ──► écran HDMI       │
 │   wayvnc :5900 (bureau à distance)      SSH :22                       │
 └───────────────────────────────────────────────────────────────────────┘
        ▲                      ▲                          ▲
   Téléphones des        PC de l'administration     PC de l'informatique
   infirmières           (statistiques)             (SSH, VNC)
```

**Un visiteur est « interne » ou « externe »** selon son adresse source (`internalNetworks`) : les accès externes sont durcis (voir [INTERNET.md](INTERNET.md)).

Fichiers principaux : voir le [README](../README.md).

## 4. Décisions techniques et leurs raisons

| Choix | Pourquoi |
|---|---|
| **Python standard, aucune dépendance** | Aucune installation ni mise à jour de paquets (le Pi n'a pas toujours `sudo`), surface d'attaque réduite, démarrage fiable |
| **Flux temps réel (SSE)** plutôt que des requêtes répétées | Écran mis à jour en moins d'une seconde, simple, supporté par tous les navigateurs |
| **SQLite pour le journal** | Fichier unique, sans service, inclus dans Python ; suffisant pour quelques changements par jour |
| **Chromium en kiosque** (relancé en boucle) | Affichage plein écran robuste ; si Chromium plante ou est fermé, il revient seul |
| **Statistiques : événements « automatiques » à part** | La fermeture de fin de journée ne doit pas compter comme un usage du bouton « Absente » |
| **Taux d'occupation = consultation ÷ présence** | Le temps « absente » inclut nuits et week-ends : il n'aurait aucun sens |
| **Appuis de moins de 10 s ignorés** | Un appui par erreur aussitôt corrigé ne doit pas fausser les chiffres |
| **Codes hachés avec scrypt** | Une copie de `config.json` ne livre pas les codes ; calcul volontairement lent contre la devinette |
| **HTTPS avec une autorité locale** | Gratuit, marche sans Internet ; l'autorité s'installe une fois par appareil. Remplaçable par un vrai certificat |
| **Autorisation vérifiée côté serveur à chaque requête** | Le navigateur n'est jamais cru : une infirmière ne peut modifier que son panneau quel que soit le contenu de sa requête |
| **Administration en lecture seule** | Elle consulte, elle ne pilote aucun panneau |
| **Blocage des essais par adresse ET par compte** | Contre la devinette ; comptés **séparément pour l'interne et l'externe** : des essais venus d'Internet ne bloquent jamais les infirmières au lycée |
| **Second facteur (TOTP) obligatoire depuis l'extérieur** | Un code à 6 chiffres peut être attaqué depuis beaucoup d'adresses ; le second facteur le rend inutile seul |
| **Rien de lisible sans connexion depuis Internet, liste des comptes masquée** | Ne pas livrer la présence du personnel ni la liste des cibles |
| **La passerelle est toujours « externe »** | Si le pare-feu cachait l'origine des visiteurs (NAT source), ils seraient pris pour des gens du lycée : cette règle l'empêche |
| **Noms `.local` à un seul niveau** (`pi-infirmerie-admin.local`) | Windows ne résout en mDNS que ces noms ; `admin.pi-infirmerie.local` ne fonctionne pas |
| **Répondeur mDNS intégré** | Annonce les noms sans installer `avahi-publish` (qui demande `sudo`) |
| **Redirection relative pour un visiteur externe** | Ne jamais renvoyer un visiteur vers un nom qu'il ne sait pas résoudre (bug corrigé le 12/10) |
| **`set-static-ip.sh` avec vérification de la passerelle** | Un changement d'IP raté ne doit pas couper le Pi ; le retour arrière est automatique |

## 5. État actuel de l'installation

### Le Pi

| | |
|---|---|
| Nom d'hôte / compte | `pi-infirmerie` / `infirmerie` |
| Système | Raspberry Pi OS (Debian 13 « trixie »), Python 3.13, Chromium 154 |
| Adresse | **`10.255.5.223/8`**, passerelle `10.255.5.202`, DNS `8.8.8.8` (ancienne adresse : `10.5.35.101`) |
| Application | `~/cabinet`, service utilisateur `cabinet.service` (démarre avec le bureau) |
| Kiosque | `~/cabinet/deploy/kiosk.sh`, lancé par `~/.config/labwc/autostart` |
| VNC | `wayvnc.service` (utilisateur `vnc`, mot de passe dans `~/.config/wayvnc/config`) |
| Ports qui écoutent | 8080 (HTTP), **8443 (HTTPS)**, 5900 (VNC), 22 (SSH) |

### Les sites et les adresses

| Usage | Adresse |
|---|---|
| Écran public (réseau interne) | `https://pi-infirmerie.local:8443/` |
| Panneau des infirmières | `https://pi-infirmerie-admin.local:8443/` |
| Statistiques | `https://pi-infirmerie-stats.local:8443/` |
| Depuis Internet (essai par l'IP) | `https://45.15.220.130:8443/admin` (avertissement de certificat attendu) |

### Les comptes

| Compte | Identifiant | Code | Second facteur |
|---|---|---|---|
| Mme Broux | `broux` | défini (haché) | **non encore inscrit** |
| Mme Glinka | `glinka` | défini (haché) | **non encore inscrit** |
| Administration | `administration` | défini (haché) | **non encore inscrit** |

### Le pare-feu et Internet (constaté le 12/10)

- `45.15.220.130:8443` répond avec **notre application** : la redirection existe déjà, et **l'adresse source des visiteurs est conservée** (vérifié avec une tentative de connexion bidon).
- `45.15.220.130:443`, `:80` et `:8080` appartiennent à **un autre serveur** (nginx) : ils ne vont pas au Pi.
- Ports sensibles testés depuis l'extérieur (22, 21, 23, 25, 3389, 445, 5900, 5353) : **fermés ou filtrés**.

### Le certificat HTTPS

Autorité locale du lycée (`certs/ca.crt`), empreinte SHA-256 :
`BE:25:FD:DC:69:75:06:70:D4:EF:08:44:63:CD:DE:1B:E0:61:D3:92:AF:65:45:65:17:8D:0F:A8:8B:5D:3F:A1`
Le certificat du serveur couvre les trois noms `.local` et l'**ancienne** adresse IP (`10.5.35.101`) : il faudra le regénérer pour qu'il porte `10.255.5.223` (voir 9).

### Le dépôt

Dépôt GitHub **privé** `rubzzz81/affichage-infirmerie`, branche `main`. Ne contient ni `config.json`, ni codes, ni clés, ni logo, ni données (`.gitignore`).

## 6. Chronologie

| Date | Étape |
|---|---|
| **9 oct. 2026** | Erreur d'écriture dans Pi Imager (causes et remèdes) ; console du switch Aruba 2530 ; premiers tests réseau et SSH du Pi |
| 9 oct. | Conception et première version : serveur Python, page d'affichage, panneau, kiosque Chromium, service systemd |
| 9 oct. | Corrections d'affichage (16:9, textes qui débordent) ; passage automatique en « Absente » à l'heure de fin |
| 9 oct. | Dépôt GitHub privé ; guide pas à pas avec captures |
| 9 oct. | **Statistiques** (journal SQLite, tableau de bord, CSV), **vue par infirmière**, titre et séparateur à l'écran, configuration multi-établissements |
| 12 oct. | **Accès à distance VNC** (wayvnc) ; erreur PuTTY trop ancien |
| 12 oct. | **Sécurisation** : comptes, codes hachés, HTTPS (autorité locale), sessions, blocage des essais, journal |
| 12 oct. | **Trois sites** (écran, panneau, statistiques) et noms sans IP (mDNS) ; sous-domaines |
| 12 oct. | **Exposition sur Internet** : second facteur, masquage, limites, HSTS, rechargement de certificat |
| 12 oct. | **Changement d'adresse IP** (10.255.5.223) : script sécurisé ; problème de masque `/24` ; redirection vers un nom `.local` corrigée |
| 12 oct. | Cette documentation |

## 7. Incidents rencontrés et leçons

| Incident | Cause | Solution et leçon |
|---|---|---|
| Pi Imager : « Error writing to storage device » | Carte, lecteur ou état du disque | Autre lecteur ou port, `diskpart clean`, formateur officiel, test de la carte ([GUIDE](GUIDE.md), 2.5) |
| PuTTY : « Couldn't agree a key exchange algorithm » | PuTTY trop ancien pour le Pi | Mettre PuTTY à jour (ou utiliser `ssh` de Windows) |
| TigerVNC : connexion refusée sur le port 59000 | `host:5900` lu comme un numéro d'écran | Saisir l'adresse **sans port** (ou `host::5900`) |
| Un second superviseur du kiosque ouvrait des fenêtres sans fin | Un `kiosk.sh` lancé à la main en plus de celui de l'autostart | Un seul superviseur ; envisager un verrou |
| Requêtes qui se « mélangeaient » (erreur 501, `{}GET`) | Un POST refusé avant lecture de son corps | Toujours lire le corps d'abord (corrigé, testé) |
| Certificat refusé par les clients stricts | L'autorité locale n'avait pas `keyUsage` ni `basicConstraints` | Extensions ajoutées (corrigé, testé) |
| `admin.pi-infirmerie.local` introuvable sous Windows | Windows ne résout en mDNS que les noms `.local` à un niveau | Noms `pi-infirmerie-admin.local` |
| `/` lent depuis l'adresse publique | Redirection vers un nom `.local` introuvable depuis Internet | Redirection relative (corrigé, testé) |
| Changement d'IP « sans effet » | Le retour arrière automatique exigeait une confirmation impossible | Le script vérifie la passerelle lui-même ; journal persistant |
| Ping qui répond mais **tous les ports TCP bloqués** | Pi en `/24`, PC en `/8` : les réponses TCP passaient par la passerelle (routage asymétrique) | Même masque `/8` ; la passerelle doit être dans le sous-réseau |
| Fichier parasite créé par un test dans le vrai dossier personnel | Un réglage de test mal pris en compte | Corrigé ; les tests utilisent un dossier temporaire |

Un **point inexpliqué** : la clé SSH `id_ed25519_pi` du PC était déjà autorisée sur le Pi dès sa création (10:59, le 9 octobre). Qui l'a installée n'a pas été établi.

## 8. Tests

| Suite | Contrôles | Ce qu'elle vérifie | Lancement |
|---|---|---|---|
| `tests/test_security.py` | 73 | Droits (infirmière, administration, inconnu), HTTPS imposé, sessions, blocage, CSRF, sous-domaines, changement de code | `python3 tests/test_security.py` dans une copie `*test*` |
| `tests/test_internet.py` | 56 | Accès externe : rien de lisible, second facteur, anti-rejeu, verrouillages séparés, limites, HSTS, port HTTP local, certificat rechargé, redirections | idem |
| `tests/test_static_ip.sh` | 19 | `set-static-ip.sh` avec de faux `nmcli` et `ping` : arguments invalides, mode à blanc, succès, retour arrière, confirmation | `sh tests/test_static_ip.sh` |

Les deux premières suites **remplacent `config.json` et `certs/`** : elles refusent de s'exécuter hors d'un dossier dont le nom contient « test ». Les codes et secrets de test sont aléatoires, gardés en mémoire, jamais affichés.

En plus, un parcours de **21 contrôles dans un vrai navigateur** (connexion, panneau, statistiques, déconnexion, aucune erreur JavaScript) a servi à produire les captures de la documentation. Il n'est pas dans le dépôt.

**Ce qui n'a pas pu être testé** : un accès depuis un vrai Internet complet (seulement depuis l'extérieur du lycée par l'adresse publique), l'émission d'un vrai certificat, `set-static-ip.sh` sur le vrai réseau avec `sudo` en dehors d'un essai réel du 12/10, les noms `.local` sur Android et iPhone, le redémarrage du Pi avec toutes les protections.

## 9. Ce qui reste à faire

Par ordre de priorité :

1. **Inscrire le second facteur** de chaque compte : `python3 deploy/pins.py totp nurse:broux` (puis `nurse:glinka`, `admin:administration`). Sans lui, aucune connexion depuis Internet.
2. **Pare-feu du lycée** : vérifier que **seule** la redirection vers le port 8443 existe, qu'aucune autre ne mène au Pi, et que le NAT source reste désactivé.
3. **Pare-feu et mises à jour du Pi** (`sudo`) : `ufw`, `unattended-upgrades` ; **limiter SSH (22) et VNC (5900)** aux postes de l'informatique ([INTERNET.md](INTERNET.md), 3.7).
4. **Un nom de domaine et un vrai certificat** : supprime l'avertissement du navigateur, permet des noms `admin.…` et `stats.…` (voir [INTERNET.md](INTERNET.md), 3.3 et 3.4).
5. **Regénérer le certificat local** pour la nouvelle adresse (`sh deploy/setup-https.sh`), tant qu'il n'est pas remplacé par un vrai.
6. **Installer l'autorité `ca.crt`** sur les appareils (page d'aide `/certificat`), si le vrai certificat tarde.
7. **Désactiver la veille de l'écran** (`sudo raspi-config`) si ce n'est pas fait.
8. **Remettre les codes en main propre** aux infirmières et à l'administration ; leur donner la fiche d'utilisation.
9. Faire le test depuis l'extérieur : `python3 tools/check-exposure.py 45.15.220.130 8443` (depuis un autre réseau).
10. Facultatif : durcir `deploy/kiosk.sh` (temporisation croissante, journal, verrou), ports 80/443 (`deploy/use-standard-ports.sh`), VPN plutôt qu'Internet ouvert.

## 10. Carte de la documentation

| Document | Pour qui, pour quoi |
|---|---|
| [README](../README.md) | Vue d'ensemble, fichiers, API |
| [GUIDE.md](GUIDE.md) | **Mise en place pas à pas**, de la carte SD à l'écran en service, avec captures |
| [SECURITE.md](SECURITE.md) | Comptes, codes, HTTPS, sessions, blocages, journal, pare-feu |
| [INTERNET.md](INTERNET.md) | Accès depuis Internet : architecture, second facteur, DNS, certificat, règles de pare-feu |
| [ADAPTER.md](ADAPTER.md) | Déployer pour **un autre établissement** |
| [EXPLOITATION.md](EXPLOITATION.md) | Tâches courantes, sauvegardes, arrivée et départ d'une infirmière, renouvellements |
| [DEPANNAGE.md](DEPANNAGE.md) | **Symptôme → cause → solution**, pour tout le projet |
| **PROJET.md** (ce document) | Mémoire du projet : décisions, état, chronologie, reste à faire |
