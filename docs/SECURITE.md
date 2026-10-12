# Sécurité et accès

Le Pi est joignable par tout le réseau du lycée, **élèves compris**. Ce document décrit
comment l'application est protégée, ce qu'il faut faire pour la mettre en service
correctement, et ce qui reste à la charge du service informatique.

## En deux minutes

| Qui | Ce qu'elle ou il peut faire | Ce qu'il lui faut |
|---|---|---|
| **Tout le monde** (élèves compris) | Voir l'écran d'affichage et l'état des infirmières, c'est-à-dire ce que montre déjà l'écran du couloir | Rien |
| **Une infirmière** | Piloter **son** panneau ; voir **ses** statistiques | Son code personnel |
| **L'administration** | Voir les statistiques de **toutes** les infirmières, en **lecture seule** (elle ne peut pas piloter un panneau) | Le code du compte administration |

- Les codes sont **personnels** et chacun est **haché** (jamais stocké en clair).
- Les mots de passe ne circulent **qu'en HTTPS** (chiffré).
- Après quelques erreurs de code, la connexion est **bloquée** quelques minutes.
- Chaque connexion, réussie ou non, est **notée** dans un journal.

Mises en service en 4 commandes (détaillées plus bas) :
```bash
sh deploy/setup-https.sh                  # 1. chiffrement (certificat)
python3 deploy/pins.py generate           # 2. un code pour chaque compte
systemctl --user restart cabinet.service  # 3. prise en compte
# 4. installer certs/ca.crt sur les appareils (une seule fois)
```

## 1. Les comptes

Les comptes sont déclarés dans `config.json` : une entrée par infirmière dans `nurses`,
une ou plusieurs entrées dans `admins` (par exemple la direction, la vie scolaire).

```json
{
  "admins": [
    { "id": "administration", "name": "Administration" }
  ],
  "nurses": [
    { "id": "broux", "name": "Mme Broux", "hours": { "default": ["08:00", "18:00"] } }
  ],
  "sessionHours": { "nurse": 12, "admin": 2 }
}
```

Un compte **sans code ne peut pas se connecter** : par défaut, rien n'est ouvert.

### Définir les codes

Les codes se définissent **sur le Pi, en SSH**, avec `deploy/pins.py`. Le script ne les affiche
jamais après coup et ne les enregistre pas en clair.

| Besoin | Commande |
|---|---|
| Voir quels comptes ont un code (sans l'afficher) | `python3 deploy/pins.py list` |
| Choisir soi-même le code d'un compte | `python3 deploy/pins.py set nurse:broux` puis saisie masquée |
| Code de l'administration | `python3 deploy/pins.py set admin:administration` |
| Générer un code aléatoire de 6 chiffres pour chaque compte sans code | `python3 deploy/pins.py generate` |
| Remplacer tous les codes | `python3 deploy/pins.py generate --all` |
| Ajouter un compte administration | `python3 deploy/pins.py add-admin viescolaire "Vie scolaire"` puis `set` |
| Supprimer un compte administration | `python3 deploy/pins.py remove-admin viescolaire` |

- **`generate` affiche les codes une seule fois, à l'écran du terminal.** Notez-les, remettez
  chaque code à son titulaire **en main propre**, puis effacez le terminal (`clear`).
- Longueur minimale : **6 caractères** (chiffres ou lettres). Les codes évidents sont refusés
  (`123456`, `000000`, un seul caractère répété...).
- La prise en compte est **immédiate**, sans redémarrage. Si le code d'un compte change, ses
  sessions déjà ouvertes sont **fermées** aussitôt (utile si un téléphone est perdu).
- **Code oublié** : lancer `set` pour ce compte. Il n'existe aucun moyen de retrouver l'ancien code.

### Qui voit quoi

| Action | Infirmière | Administration | Sans compte |
|---|---|---|---|
| Voir l'écran d'affichage (`/`) | oui | oui | oui |
| Piloter son panneau (`/admin`) | **oui, le sien** | non | non |
| Piloter le panneau d'une autre | non (refusé) | non | non |
| Statistiques de toutes (`/stats`) | non (les siennes seulement) | **oui** | non |
| Exports CSV | les siens | tous | non |

## 2. Le chiffrement (HTTPS)

Sans HTTPS, un élève qui « écoute » le réseau pourrait lire un code au moment de la connexion.
C'est pourquoi les pages `/admin` et `/stats` et toutes les API de connexion ne sont servies
**qu'en HTTPS** : en HTTP, elles redirigent vers HTTPS ou sont refusées. Seul l'écran public
(`/`, `/api/state`) reste en HTTP, car il ne contient que ce que montre déjà l'écran du couloir.

### Mise en place (une seule fois)

Sur le Pi :
```bash
sh deploy/setup-https.sh
systemctl --user restart cabinet.service
```
Le script crée une **petite autorité de certification privée** du lycée (`certs/ca.crt`) et
un **certificat** du Pi signé par elle, valable pour `pi-infirmerie`, `pi-infirmerie.local` et
l'adresse IP du Pi. Pour ajouter un nom donné par le service informatique :
`sh deploy/setup-https.sh infirmerie.lycee-rascol.fr`.

Le serveur écoute alors en HTTPS sur le port **8443** (réglable : `tls.port` dans `config.json`).

### Installer l'autorité sur chaque appareil (une seule fois)

Sans cette étape, le navigateur affiche un avertissement « connexion non sécurisée ». Une fois
`ca.crt` installé, il n'y en a plus du tout. Le fichier est téléchargeable depuis l'écran du Pi,
mais **vérifiez toujours son empreinte** avant de l'installer (commande plus bas).

| Appareil | Marche à suivre |
|---|---|
| **Windows** (PC de l'administration) | Ouvrir la page d'aide `http://pi-infirmerie.local:8080/certificat` (téléchargement + empreinte), ou copier `ca.crt`, puis double-cliquer, *Installer le certificat…*, *Utilisateur actuel*, *Placer tous les certificats dans le magasin suivant* → **Autorités de certification racines de confiance**. Chrome et Edge l'utilisent. Pour **Firefox** : *Paramètres → Vie privée et sécurité → Certificats → Afficher les certificats → Autorités → Importer*. |
| **Android** | Télécharger `ca.crt`, puis *Paramètres → Sécurité → Chiffrement et identifiants → Installer un certificat → Certificat d'autorité de certification*. |
| **iPhone / iPad** | Télécharger `ca.crt`, l'installer dans *Réglages → Profil téléchargé*, puis activer la confiance dans *Réglages → Général → Informations → Réglages des certificats*. |
| **Poste du lycée géré par le service informatique** | Le service informatique peut déployer `ca.crt` par stratégie de groupe (GPO), une seule fois pour tous les postes. |

**Vérifier l'empreinte** (à faire avant d'installer) : sur le Pi,
```bash
openssl x509 -in ~/cabinet/certs/ca.crt -noout -fingerprint -sha256
```
et comparer avec celle de l'appareil (sous Windows : propriétés du certificat → *Détails* →
*Empreinte*). Si elles diffèrent, **n'installez pas** le fichier.

> `certs/ca.key` (clé de l'autorité) et `certs/server.key` sont **secrets** : droits 600, jamais
> copiés ni publiés (le dossier `certs/` est exclu du dépôt). Qui possède `ca.key` pourrait fabriquer des certificats acceptés par les appareils
> qui font confiance à l'autorité. Vous pouvez la mettre hors ligne (clé USB) une fois le
> certificat du serveur créé ; elle ne sert qu'à renouveler le certificat (825 jours).

### Renouveler le certificat

Le certificat du serveur est valable **825 jours** (environ 2 ans et 3 mois). Avant son échéance :
```bash
sh deploy/setup-https.sh
systemctl --user restart cabinet.service
```
L'autorité (`ca.crt`) est conservée : les appareils n'ont **rien** à refaire.

## 3. Trois sites, trois adresses, sans taper l'IP

Chaque usage a **son propre site**, repérable par son nom. C'est plus simple à retenir et
cela sépare ce que voient les élèves de ce qui est protégé :

| Site | Adresse | Qui | Contenu |
|---|---|---|---|
| **Écran public** | `https://pi-infirmerie.local:8443/` | Tout le monde | L'écran d'affichage (présence uniquement) |
| **Panneau des infirmières** | `https://pi-infirmerie-admin.local:8443/` | Infirmières (code personnel) | Le panneau de chacune |
| **Statistiques** | `https://pi-infirmerie-stats.local:8443/` | Administration, et chaque infirmière pour ses chiffres | Tableau de bord, exports |

- Les anciens liens (`…/admin`, `…/stats`, et l'accès par l'adresse IP) **continuent de fonctionner** : depuis l'écran public, ils redirigent vers le bon site.
- Les noms `.local` sont **annoncés automatiquement par le Pi** sur le réseau (mDNS) : rien à demander au service informatique, rien à installer. Ils fonctionnent sous Windows 10 ou plus, iPhone, Mac et la plupart des Android récents. Si un appareil ne les trouve pas, utiliser l'adresse IP.
- **Pourquoi `pi-infirmerie-admin.local` et pas `admin.pi-infirmerie.local` ?** Windows ne retrouve en mDNS que les noms à **un seul niveau** (`quelquechose.local`). Les vrais sous-domaines ne marchent qu'avec un serveur DNS (voir plus bas).
- Le certificat HTTPS couvre automatiquement ces trois noms ; `sh deploy/setup-https.sh` le met à jour.

### Avec un vrai nom DNS (recommandé si le service informatique le propose)

Pour un nom de lycée comme `infirmerie.lycee-rascol.fr`, demander au service informatique **trois enregistrements** vers l'adresse du Pi (ou un seul enregistrement « joker » `*.infirmerie.lycee-rascol.fr`), puis dans `config.json` :
```json
"domain": "infirmerie.lycee-rascol.fr"
```
Le panneau est alors sur `admin.infirmerie.lycee-rascol.fr` et les statistiques sur `stats.infirmerie.lycee-rascol.fr`, avec de **vrais sous-domaines**. Ils partagent la connexion (une seule saisie du code pour le panneau et les statistiques d'une infirmière). Lancer ensuite :
```bash
sh deploy/setup-https.sh        # le certificat couvre les nouveaux noms
systemctl --user restart cabinet.service
```
Pour choisir d'autres noms : `"hosts": { "admin": "panneau.lycee-rascol.fr", "stats": "chiffres.lycee-rascol.fr" }`.

### Sans le numéro de port

Passer en ports standards (80 et 443) : `sh deploy/use-standard-ports.sh` (demande le mot de passe `sudo`). Les adresses deviennent `https://pi-infirmerie-admin.local` etc. Annulation : `--undo`.

### Enregistrer l'adresse sur le téléphone

Chaque infirmière enregistre le panneau comme **favori** ou l'ajoute à l'**écran d'accueil** : un seul geste ensuite. Un **QR code** vers l'adresse du panneau, collé près de l'écran, évite toute saisie.

> La connexion reste personnelle : un favori ou un QR code ouvre la page de connexion, jamais un panneau.

### « Connexion non sécurisée » dans le navigateur ?

Les pages protégées sont **toujours** servies en HTTPS. Un avertissement du navigateur signifie seulement que l'appareil ne connaît pas encore l'autorité de certification du lycée : il suffit de l'installer **une fois**. Une page d'aide guide cette étape, avec l'empreinte à vérifier :

```
http://pi-infirmerie.local:8080/certificat
```
(à partir de n'importe quel appareil ; elle s'ouvre aussi en HTTPS une fois l'avertissement accepté.)

## 4. Protection contre les essais de codes

Un élève pourrait essayer des codes au hasard. L'application s'en protège :

| Situation | Effet |
|---|---|
| **5 codes faux** depuis la même adresse | Cette adresse est bloquée **5 minutes** pour tous les comptes ; le blocage double à chaque récidive (jusqu'à 1 heure) |
| **20 codes faux** sur un compte en 15 minutes (toutes adresses confondues) | Le compte est bloqué **15 minutes** |
| Pendant un blocage | Même le **bon** code est refusé ; le message indique le temps d'attente |
| Compte inexistant, ou sans code | Même réponse qu'un mauvais code : on ne peut pas deviner les comptes |
| Le code est vérifié par | Un calcul volontairement lent (scrypt), identique que le compte existe ou non |

Avec 6 chiffres (1 million de possibilités), ces limites rendent la devinette pratiquement impossible. **Choisissez malgré tout des codes qui ne sont pas des dates ou des suites.**

Un blocage ne concerne que l'ouverture de session. L'écran du couloir et les sessions déjà ouvertes continuent de fonctionner.

## 5. Les sessions

| | Infirmière | Administration |
|---|---|---|
| Durée sans activité avant déconnexion | **12 heures** | **2 heures** |
| Durée maximale absolue | 24 heures | 24 heures |

Réglable : `"sessionHours": { "nurse": 12, "admin": 2 }` dans `config.json`.

- La session est un **cookie** aléatoire de 256 bits, `HttpOnly` (illisible par un script), `SameSite=Strict`
  (non envoyé depuis un autre site) et `Secure` (jamais en clair).
- **Se déconnecter** (lien en bas de la page) ferme la session immédiatement.
- Un redémarrage du serveur déconnecte tout le monde (les sessions sont en mémoire).
- Sur un **téléphone partagé** ou un PC commun, **toujours se déconnecter**.

## 6. Le journal des connexions

Chaque connexion, chaque échec et chaque blocage sont inscrits dans `~/cabinet/auth.log`
(lisible par le seul compte du Pi, 1 Mo puis rotation). Jamais un code n'y figure.

```
2026-10-12 15:47:28 [auth] échec de connexion nurse:broux depuis 127.0.0.1
2026-10-12 15:48:02 [auth] connexion nurse:broux depuis 10.5.35.57
2026-10-12 15:50:10 [auth] adresse 10.5.40.12 bloquée 300 s (échecs répétés)
```
```bash
tail -50 ~/cabinet/auth.log          # dernières lignes
grep "échec" ~/cabinet/auth.log      # tentatives ratées
grep "bloquée" ~/cabinet/auth.log    # blocages (tentative de devinette probable)
```
Des échecs répétés depuis une adresse inconnue sont à signaler à la direction et au service informatique.

## 6 bis. Les protections intégrées au serveur

| Protection | Rôle |
|---|---|
| HTTPS imposé pour tout ce qui est protégé | Les codes et les cookies ne circulent pas en clair |
| Autorisation vérifiée **côté serveur** à chaque requête | Une infirmière ne peut modifier que son panneau, quel que soit le contenu de sa requête |
| Origine des requêtes contrôlée + cookie `SameSite=Strict` + JSON obligatoire | Empêche qu'un site tiers déclenche une action au nom d'une infirmière connectée (CSRF) |
| En-têtes `Content-Security-Policy`, `X-Frame-Options`, `nosniff`, `Referrer-Policy` | Pages non intégrables dans un autre site, contenu non détourné |
| Corps des requêtes limité, délai de connexion borné | Contre les envois volumineux ou les connexions qui traînent |
| Codes hachés par scrypt, `config.json` en droits 600 | Une copie du fichier ne livre pas les codes |
| Aucun compte sans code | Rien n'est ouvert par défaut |

Ces protections sont vérifiées par une suite de **56 contrôles automatiques** (`tests/test_security.py`, à lancer dans une copie du dossier) et par des essais dans un navigateur :
sans session, avec une session d'infirmière, avec une session
d'administration, avec un mauvais code, avec une origine étrangère, après un changement de code, etc.

## 7. Ce que l'application ne peut pas faire seule (service informatique)

L'application protège **ses** pages. Le Pi expose aussi d'autres services au réseau du lycée :

| Service | Port | Risque | Mesure recommandée |
|---|---|---|---|
| **SSH** | 22 | Un élève peut essayer de deviner le mot de passe du compte `infirmerie` | Limiter le port 22 aux PC de l'informatique (pare-feu), idéalement n'autoriser que les **clés SSH** (`PasswordAuthentication no`) |
| **VNC** (bureau à distance) | 5900 | Idem, avec la prise de contrôle de l'écran à la clé | Limiter le port 5900 aux PC autorisés, **ou** le faire écouter en local seulement et passer par un tunnel SSH |
| **Application** | 80/443 (ou 8080/8443) | Protégée par ce document | Ouvrir à tout le réseau (les infirmières y accèdent depuis leur téléphone) |

**Le plus simple : un pare-feu sur le Pi** (aucun n'est installé par défaut, il faut `sudo`) :
```bash
sudo apt install ufw
sudo ufw default deny incoming
sudo ufw allow 8080/tcp          # écran public (ou 80)
sudo ufw allow 8443/tcp          # panneaux et statistiques (ou 443)
sudo ufw allow from 10.5.35.10 to any port 22 proto tcp      # SSH : le PC de l'informatique
sudo ufw allow from 10.5.35.10 to any port 5900 proto tcp    # VNC : le même PC
sudo ufw enable
```
Remplacez `10.5.35.10` par l'adresse du PC d'administration. **Attention** : si vous vous
trompez d'adresse pour SSH, vous ne pourrez plus vous connecter à distance ; gardez un clavier et un écran pour réparer.

Le service informatique peut aussi filtrer ces ports sur le réseau (VLAN, ACL du switch), ce qui évite d'installer un pare-feu sur chaque Pi.

## 8. Limites connues

- **L'écran public est lisible par tous.** Il ne montre que la présence des infirmières et leurs horaires, comme l'écran du couloir.
- **Un code partagé n'existe plus.** En contrepartie, si une infirmière donne son code à quelqu'un, l'application ne peut pas l'empêcher : rappelez que le code est personnel.
- **Pas de double authentification.** Le code est le seul facteur.
- **Accès physique au Pi** : qui peut débrancher la carte SD ou brancher un clavier contourne tout. Placez le Pi derrière l'écran, dans un local fermé.
- **Statistiques** : l'administration voit l'activité de chaque infirmière. Informez les personnes concernées de cette mesure, et conservez les données conformément à la réglementation en vigueur sur les données personnelles (RGPD) ; la base `data.db` ne contient que des états et des horaires, jamais de nom d'élève.
- **Pas de journal des modifications d'états par personne** au-delà de l'historique d'états de chaque infirmière : chaque panneau ne peut être modifié que par sa propriétaire.

## 9. Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| « Ce compte n'est pas activé : aucun code défini » | Le compte n'a pas encore de code | `python3 deploy/pins.py set <compte>` |
| « Code incorrect » | Mauvais code, ou majuscules/clavier | Ressaisir ; sinon redéfinir le code avec `pins.py set` |
| « Trop de tentatives. Réessayez dans X min » | Blocage d'adresse ou de compte | Attendre. Pour débloquer tout de suite : `systemctl --user restart cabinet.service` |
| Avertissement « connexion non sécurisée » | `ca.crt` pas installé sur cet appareil | Installer l'autorité (section 2) |
| La page redirige vers un site inaccessible | HTTPS actif mais le port 8443 est filtré | Autoriser le port 8443 (ou 443) sur le réseau |
| `/admin` ne s'ouvre pas du tout après `setup-https.sh` | Le serveur n'a pas été redémarré | `systemctl --user restart cabinet.service` |
| Déconnexion à chaque ouverture | Navigateur qui refuse les cookies, ou navigation privée | Autoriser les cookies pour ce site |
| « Connexion HTTPS requise » | Page ouverte en `http://` | Utiliser `https://...` |
| Message « Session expirée » | Plus de 12 h (2 h pour l'administration) sans activité | Se reconnecter |
