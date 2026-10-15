# Accès depuis Internet

Ce guide décrit comment rendre le panneau des infirmières joignable depuis **Internet** (pour les
infirmières qui ne sont pas sur le même réseau que le Pi), **en limitant les risques**.

> **À lire d'abord.** Ouvrir un port sur Internet expose le Pi à des tentatives d'attaque
> automatiques, sans interruption, dès les premières minutes. Un **VPN** (WireGuard, OpenVPN ou
> IPsec sur le pare-feu du lycée) reste l'option **la plus sûre** : le Pi n'est alors jamais exposé.
> Ce guide décrit l'autre option, l'exposition d'un seul port, avec toutes les protections
> prévues. Il ne la rend pas équivalente à un VPN.

## 1. Architecture visée

```
 Infirmière (4G, domicile)              Pare-feu du lycée                    Réseau isolé (DMZ)
 ───────────────────────►  203.0.113.10 : 443   ──redirection──►   Pi 192.168.1.50 : 8443
        HTTPS chiffré                  (un seul port)                (passerelle 192.168.1.1)
```

- **Un seul port** traverse le pare-feu : le port **HTTPS**. Rien d'autre (jamais SSH 22, VNC 5900, HTTP 8080).
- Côté Internet, on conseille le port **443** (les infirmières tapent `https://admin.infirmerie.<domaine>` sans numéro de port), redirigé vers le port **8443** du Pi. Si 443 est déjà utilisé, utiliser 8443 : l'adresse porte alors `:8443`.
- Tout passe par ce port : panneau, statistiques et connexion. **L'écran public n'est pas servi aux inconnus.**
- Le Pi est idéalement dans un **réseau isolé** (DMZ) : il n'a pas à joindre le reste du réseau du lycée.

### Défense en profondeur : trois niveaux

| Niveau | Ce qu'il fait | Qui s'en occupe |
|---|---|---|
| **Pare-feu du lycée** | Ne laisse entrer que le port HTTPS ; limite le débit ; filtre par pays si possible ; journalise | Service informatique |
| **Pi** | Seul le port HTTPS écoute le réseau ; pare-feu local ; mises à jour de sécurité | Vous (avec `sudo`) |
| **Application** | Aucun contenu sans connexion, second facteur, blocage des essais, limites de connexions, HTTPS strict | Déjà en place |

Chaque niveau protège si un autre échoue. **Ne vous reposez jamais sur un seul.**

## 2. Ce que l'application fait différemment pour Internet

Une requête est **externe** si son adresse source n'est pas dans le réseau interne (`internalNetworks`,
par défaut les adresses privées `10.x`, `172.16-31.x`, `192.168.x`). Le Pi lui-même (kiosque) est toujours de confiance.

| | Depuis le réseau interne | Depuis Internet |
|---|---|---|
| Écran public, état en temps réel | lisible | **refusé sans connexion** |
| Liste des comptes (noms) | affichée pour choisir son nom | **masquée** : on saisit son identifiant |
| Connexion | code personnel | code personnel **+ code de l'application d'authentification** (second facteur, obligatoire) |
| Compte sans second facteur | peut se connecter | **ne peut pas** se connecter depuis Internet |
| Message d'erreur | précis (« code incorrect ») | **toujours le même** : on ne sait pas quel élément est faux |
| Blocage après essais ratés | 5 essais / adresse, 20 / compte | 5 essais / adresse, **10 / compte**, **comptés à part** |
| Durée de session | 12 h (infirmière), 2 h (administration) | **4 h** et **1 h** |
| Connexions simultanées par adresse | 100 | **20** (et 3 flux temps réel) |
| Page d'aide au certificat | accessible | introuvable |

Points importants :
- **Les échecs venus d'Internet ne bloquent jamais les infirmières au lycée.** Sans cette séparation, un attaquant pourrait verrouiller les comptes en tapant de faux codes.
- Le second facteur ne peut pas être rejoué : un code déjà utilisé, ou plus ancien que le dernier accepté, est refusé.
- **Le pare-feu doit conserver l'adresse source réelle des clients** (pas de NAT source / masquerade). L'application traite de plus **l'adresse de la passerelle comme externe** : si, par erreur, le pare-feu cachait l'origine des visiteurs, ils seraient quand même traités comme venant d'Internet (et non comme des gens du lycée). Voir la vérification en 5.8.

## 3. Mise en service, dans l'ordre

**Ordre conseillé : tout régler et tester à l'intérieur, puis ouvrir au dernier moment.**

### 3.1 Comptes, codes et second facteur (avant tout)

Sur le Pi, en SSH :
```bash
cd ~/cabinet
python3 deploy/pins.py generate               # codes personnels (notez-les)
python3 deploy/pins.py totp nurse:dupont       # second facteur, un compte après l'autre
python3 deploy/pins.py totp nurse:martin
python3 deploy/pins.py totp admin:administration
python3 deploy/pins.py list                   # contrôle : « Second facteur : activé » partout
```
Pour chaque personne : installer sur son téléphone une application d'authentification (FreeOTP, Aegis,
Google Authenticator, Microsoft Authenticator, 2FAS...), ajouter la clé affichée en **saisie manuelle**,
type « basé sur le temps », puis saisir le code à 6 chiffres affiché pour confirmer. **Rien n'est enregistré
tant que ce code n'est pas bon.** Remettre les codes **en main propre**.

> Perte du téléphone : refaire `python3 deploy/pins.py totp <compte>` (nouvelle clé : l'ancienne ne marche plus) et changer le code (`set`).
> Les secrets du second facteur sont dans `config.json` (droits 600, jamais publié) : sauvegardez ce fichier de façon sûre.

### 3.2 Changer l'adresse du Pi (192.168.1.50, passerelle 192.168.1.1)

Le Pi est aujourd'hui en `10.0.0.40/8`. **À faire de préférence devant le Pi** (clavier + écran) : la connexion SSH ou VNC est coupée au changement,
et **votre PC peut ne plus joindre le Pi** s'il n'est pas sur le même réseau que la nouvelle adresse. C'est normal : ce n'est pas un échec.

1. **Essai à blanc** (aucune modification, ne demande pas `sudo`) : il lit la configuration actuelle et contrôle vos valeurs.
   ```bash
   sh ~/cabinet/deploy/set-static-ip.sh 192.168.1.50/24 192.168.1.1 --dry-run
   ```
2. **Le vrai changement** (demande le mot de passe `sudo`) :
   ```bash
   sh ~/cabinet/deploy/set-static-ip.sh 192.168.1.50/24 192.168.1.1
   ```
3. **Lire le résultat** (il survit au redémarrage) :
   ```bash
   cat ~/cabinet-ip.log
   hostname -I
   ```

**Ce que fait le script**
- **Refuse les fautes évidentes** avant de toucher à quoi que ce soit : adresse sans masque, adresse invalide, et surtout une **passerelle qui n'est pas dans le même sous-réseau** que l'adresse (le cas qui coupe le Pi du réseau).
- **Sauvegarde** l'ancienne configuration dans `~/cabinet-ip-backup.txt`, avec la commande pour revenir en arrière à la main.
- **Applique** la nouvelle adresse, puis **vérifie que la passerelle répond** (ping, pendant 30 secondes). Si elle répond : terminé. Sinon, l'ancienne configuration est **restaurée automatiquement**.
- Relance ensuite l'application (pour que l'annonce du nom `.local` suive la nouvelle adresse).

> **Le masque** (`/24` ci-dessus) est à confirmer avec le service informatique : `/24` = `255.255.255.0`, `/8` = `255.0.0.0`.
> Avec `/24`, l'adresse et la passerelle doivent partager les trois premiers nombres (`192.168.1.x`).
>
> **Si l'adresse revient à l'ancienne** : lisez `~/cabinet-ip.log`. La ligne `ÉCHEC - la passerelle … ne répond pas` signifie que le Pi n'a pas pu joindre
> la passerelle sur ce réseau (câble branché sur le mauvais réseau, mauvaise passerelle, adresse déjà prise, port du switch dans un autre VLAN) : le retour arrière est alors voulu.
> Option `--confirm` : exige en plus un `touch /tmp/cabinet-ip-ok` dans les 120 s.
>
> **Le câble réseau** doit être branché sur le réseau qui porte `192.168.1.0/24` (celui de la passerelle `192.168.1.1`) *avant* de lancer le changement.

Ensuite : mettre à jour le DNS interne, les favoris et `~/.ssh/known_hosts`, et **relancer `sh deploy/setup-https.sh`** si vous utilisez encore le certificat local
(il porte l'ancienne adresse). L'écran du couloir n'a rien à changer (il utilise `localhost`).
### 3.3 Les noms DNS (domaine du lycée)

Créer, chez l'hébergeur du domaine, les enregistrements **A** vers l'adresse publique **203.0.113.10** :

| Nom | Rôle |
|---|---|
| `admin.infirmerie.<domaine>` | panneau des infirmières |
| `stats.infirmerie.<domaine>` | statistiques |
| `infirmerie.<domaine>` | nom de base |

(Ou un seul enregistrement « joker » `*.infirmerie.<domaine>`.) Pour les ordinateurs **du lycée**, prévoir sur le DNS interne
les mêmes noms vers `192.168.1.50` (« split DNS »), sinon leurs requêtes passeraient par l'adresse publique et exigeraient une
traduction « en épingle » (hairpin NAT) sur le pare-feu.

### 3.4 Un vrai certificat (recommandé)

Avec un domaine, un **vrai certificat gratuit** (Let's Encrypt) évite d'installer notre autorité sur chaque téléphone.
La méthode adaptée à **un seul port** est la validation **DNS-01** : elle ne demande aucun accès entrant au Pi.

Avec `acme.sh` (installé sans `sudo` dans `~/.acme.sh`), sur le Pi :
```bash
# 1. installer acme.sh (le Pi doit avoir accès à Internet en sortie) ; lisez le script avant de l'exécuter
curl -fsSL https://get.acme.sh -o /tmp/acme-install.sh && less /tmp/acme-install.sh && sh /tmp/acme-install.sh email=admin@<domaine>

# 2. obtenir le certificat (exemple avec une API DNS ; la liste des hébergeurs compatibles est dans la documentation d'acme.sh)
export <VARIABLES-DE-L-API-DE-VOTRE-HÉBERGEUR>        # ex. OVH_AK, OVH_AS... ; voir la doc d'acme.sh
~/.acme.sh/acme.sh --issue --dns dns_<hébergeur> -d infirmerie.<domaine> -d '*.infirmerie.<domaine>'

# 3. l'installer pour l'application (renouvellement automatique tous les ~60 jours)
~/.acme.sh/acme.sh --install-cert -d infirmerie.<domaine> \
    --fullchain-file ~/cabinet/certs/server.crt --key-file ~/cabinet/certs/server.key
chmod 600 ~/cabinet/certs/server.key
```
- **L'application recharge le certificat toute seule en moins de 15 secondes**, sans redémarrage : les renouvellements sont donc transparents.
- Sans API DNS chez votre hébergeur, `acme.sh --issue --dns` en mode manuel affiche un enregistrement `TXT` à créer, mais **il faudra le refaire à chaque renouvellement**.
- Si vous obtenez le certificat autrement (service informatique, autre autorité) : `sh deploy/install-cert.sh chaine-complete.pem cle-privee.pem` (il vérifie que la clé correspond et que le certificat n'expire pas).
- **Ne relancez plus `deploy/setup-https.sh`** après cela : il remettrait le certificat local.
- Je n'ai pas pu tester l'émission d'un vrai certificat (pas d'accès à votre domaine) ; les scripts d'installation et le rechargement automatique sont, eux, testés.

### 3.5 Réglages de l'application

Dans `~/cabinet/config.json`, adapter (le reste est conservé) :
```json
{
  "domain": "infirmerie.<domaine>",
  "httpBind": "127.0.0.1",
  "totp": "external",
  "sessionHoursExternal": { "nurse": 4, "admin": 1 },
  "publicDisplay": "internal",
  "internalNetworks": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
  "externalAddresses": [],
  "tls": { "port": 8443, "cert": "certs/server.crt", "key": "certs/server.key" }
}
```
| Réglage | Effet |
|---|---|
| `domain` | Noms des sites : `admin.<domaine>` et `stats.<domaine>` (vrais sous-domaines, connexion partagée) |
| `httpBind: "127.0.0.1"` | Le port HTTP 8080 n'est plus joignable que depuis le Pi (kiosque) : **un seul port exposé**, le HTTPS |
| `totp` | `"external"` (défaut) : second facteur exigé depuis Internet ; `"always"` : aussi au lycée ; `"never"` : jamais (déconseillé : le code seul suffit, même depuis Internet) |
| `sessionHoursExternal` | Durée d'inactivité avant déconnexion, depuis Internet |
| `publicDisplay` | `"internal"` : l'écran public est lisible au lycée ; `"local"` : seulement sur l'écran du Pi ; `"everyone"` : aussi depuis Internet (déconseillé : présence et postes visibles de tous). Depuis Internet, `/` affiche une page d'accueil avec les liens `/admin` et `/stats` ; l'écran n'y apparaît que dans le dernier cas |
| `internalNetworks` | Ce qui est « interne ». **À ajuster** selon les plages réelles du lycée (si les élèves ou le Wi-Fi invité ont des adresses privées, voir la note ci-dessous) |
| `externalAddresses` | Adresses **toujours externes** (en plus de la passerelle, automatique). Mettre ici l'adresse du pare-feu si elle n'est pas la passerelle |

Puis : `systemctl --user restart cabinet.service`.

> **`internalNetworks` et le réseau des élèves.** Toute adresse privée est « interne » : elle peut lire l'écran public et ouvrir une session
> sans second facteur. Si les élèves sont sur un réseau privé joignable depuis le Pi, c'est **la situation d'avant**. Pour que seule la
> plage du personnel soit « interne », listez uniquement celle-ci (ex. `["10.5.0.0/16"]`) : tout le reste sera traité comme Internet.

### 3.6 Règles du pare-feu du lycée

| Règle | Détail |
|---|---|
| **Entrée** (redirection de port) | TCP `203.0.113.10:443` → `192.168.1.50:8443`. **Uniquement celle-ci.** |
| **Pas de NAT source** | Le Pi doit voir l'adresse **réelle** du visiteur. Désactiver le « masquerade / SNAT » sur cette règle |
| **Tout le reste refusé** | Aucune redirection pour 22 (SSH), 5900 (VNC), 8080, 80, 3389... |
| **Limitation de débit** | Par exemple 30 nouvelles connexions par minute et par adresse source ; plafond global de connexions simultanées |
| **Filtrage géographique** (si disponible) | N'autoriser que la France (ou les pays utiles) : supprime l'essentiel des attaques automatiques |
| **Réseau du Pi (DMZ)** | Le Pi ne doit pas pouvoir ouvrir de connexions vers le réseau interne. Sorties utiles seulement : DNS, NTP (heure), HTTPS vers Internet (mises à jour, renouvellement du certificat) |
| **Administration du Pi** | SSH (22) et VNC (5900) du Pi **uniquement depuis les postes de l'informatique**, côté réseau interne |
| **Journaux** | Activer la journalisation des connexions refusées et acceptées sur cette règle |

### 3.7 Pare-feu et mises à jour du Pi (nécessite `sudo`)

Même si le pare-feu du lycée filtre, le Pi doit se défendre seul. **Une fois le Pi exposé, les mises à jour de sécurité ne sont plus facultatives.**
```bash
# mises à jour (à refaire chaque mois, et activer les mises à jour automatiques)
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y unattended-upgrades ufw

# pare-feu local : seul le port de l'application est ouvert à tous
sudo ufw default deny incoming
sudo ufw allow 8443/tcp
sudo ufw allow from <IP-du-PC-de-l-informatique> to any port 22 proto tcp
sudo ufw enable
```
- **Le VNC** : le faire écouter en local seulement (`address=127.0.0.1` dans `~/.config/wayvnc/config`, puis `systemctl --user restart wayvnc.service`) et s'y connecter par un tunnel SSH : `ssh -L 5900:127.0.0.1:5900 infirmerie@<adresse>` puis, dans le client VNC, `localhost::5900`.
- **SSH** : idéalement par **clés seulement** (`PasswordAuthentication no` dans `/etc/ssh/sshd_config`, puis `sudo systemctl restart ssh`) : un mot de passe peut être deviné, une clé non. Gardez une session ouverte pendant ce test.
- Si le port 8443 est remplacé par 443 sur le Pi (`sh deploy/use-standard-ports.sh`), adapter la règle `ufw`.

### 3.8 Vérifier de l'extérieur

**Depuis une machine HORS du réseau du lycée** (téléphone en 4G avec Termux, PC à la maison...) :
```bash
python3 tools/check-exposure.py 203.0.113.10 443 --verifie-certificat
# ou, avec le nom : python3 tools/check-exposure.py admin.infirmerie.<domaine> 443 --verifie-certificat
```
Le script contrôle (sans rien tenter d'offensif) : les ports joignables (**seul le port HTTPS doit répondre**), ce qu'un inconnu peut lire
(**rien**), le chiffrement et les en-têtes de sécurité. Code de sortie 0 si tout est conforme. **Un « PROBLÈME » sur SSH ou VNC
signifie que le pare-feu les laisse passer : corrigez avant d'aller plus loin.**

Contrôle de l'adresse source (à faire une fois la redirection active) : connectez-vous depuis un téléphone en 4G, puis sur le Pi :
```bash
tail -3 ~/cabinet/auth.log
```
La ligne doit montrer **l'adresse publique du téléphone** et « (externe) ». Si elle montre l'adresse du pare-feu (`192.168.1.1`) : le pare-feu
masque l'origine des visiteurs (NAT source). L'application les traite alors comme externes (c'est la protection voulue) mais le blocage par
adresse perd son sens : **demandez de supprimer le NAT source**.

**Tester par l'adresse IP publique.** Vous pouvez joindre le panneau par `https://203.0.113.10:8443/admin` (ou `/stats`) avant que le DNS existe.
Le navigateur affiche alors un avertissement de certificat : il est normal tant que le certificat ne couvre pas cette adresse (voir 3.4).
L'application **reste sur l'adresse tapée** : elle ne renvoie jamais un visiteur vers un nom `.local` qu'il ne sait pas résoudre depuis Internet.
**Attention au port :** le port 443 de cette adresse peut être pris par un autre service (ici un nginx) ; il faut alors garder `:8443`.

### 3.9 Ouvrir progressivement

1. Ouvrir d'abord la redirection **pour votre seule adresse IP de test** (règle du pare-feu limitée à une adresse), et vérifier de bout en bout.
2. Puis l'élargir aux infirmières (adresses connues, ou France).
3. Ne l'ouvrir à tout Internet qu'en dernier, si c'est nécessaire.

## 4. Surveillance

```bash
tail -50 ~/cabinet/auth.log          # dernières connexions
grep "(externe)" ~/cabinet/auth.log | grep échec    # tentatives ratées depuis Internet
grep "bloquée\|verrouillé" ~/cabinet/auth.log       # blocages (une attaque en cours)
```
Quelques échecs isolés sont normaux (fautes de frappe). **Des dizaines d'échecs venant d'une ou plusieurs adresses inconnues** signalent une attaque :
le blocage fonctionne, mais prévenez le service informatique, qui peut bloquer ces adresses sur le pare-feu.

## 5. En cas d'incident

| Situation | Que faire |
|---|---|
| Un code est connu d'un tiers | `python3 deploy/pins.py set <compte>` : le nouveau code **ferme aussitôt** les sessions ouvertes du compte |
| Un téléphone avec le second facteur est perdu ou volé | `python3 deploy/pins.py totp <compte>` (nouvelle clé) **et** `set` (nouveau code) |
| Suspicion de compromission du Pi | **Couper la redirection sur le pare-feu**, débrancher le réseau, puis : relever `auth.log`, changer **tous** les codes et secrets, regénérer le certificat si la clé a pu fuir, réinstaller la carte SD à partir du dépôt |
| Attaque en cours (saturation) | Réduire le débit sur le pare-feu ; en dernier recours, couper la redirection. L'application limite déjà les connexions par adresse |
| Le certificat expire | `~/.acme.sh/acme.sh --renew -d infirmerie.<domaine> --force` ; le serveur le recharge seul |

## 6. Limites : ce que ce guide ne garantit pas

- **Exposer un service sur Internet comporte toujours un risque.** Le serveur est écrit avec la bibliothèque standard de Python, sans dépendance
  externe, ce qui réduit la surface d'attaque, mais **il n'a pas été audité par un tiers**. Une faille inconnue reste possible.
- **Les mises à jour** du système (Python, OpenSSL) dépendent de vous : sans elles, la sécurité se dégrade avec le temps.
- Une **attaque volumétrique** (saturer la ligne) se traite chez l'opérateur ou sur le pare-feu, pas sur le Pi.
- **Le second facteur protège les comptes, pas le serveur** : il n'empêche pas l'exploitation d'une faille logicielle.
- Le **VPN** reste plus sûr. Si les infirmières utilisent déjà un téléphone d'établissement, c'est la voie à privilégier à terme.
- **Données personnelles** : les statistiques d'activité concernent le personnel. Informez les personnes concernées, limitez l'accès (déjà le cas : comptes
  personnels), et conservez les données selon les règles en vigueur (RGPD). La base `data.db` ne contient aucun nom d'élève.
- Je n'ai pas pu tester **depuis un vrai accès Internet** ni sur votre pare-feu ; les comportements « externes » sont vérifiés par des tests qui simulent
  une adresse externe (52 contrôles automatiques dans `tests/test_internet.py`). Faites le test 3.8 une fois la redirection en place.
