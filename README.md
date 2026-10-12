# Affichage de présence des infirmeries - Lycée Louis Rascol

Un grand écran dans le couloir indique en temps réel si chaque infirmière est
présente dans son cabinet, avec ses horaires du jour. Chaque infirmière change
son état en deux touches depuis son téléphone ou son PC.

- **Libre et gratuit** (licence MIT), **aucune dépendance** : Python 3 standard uniquement.
- Fonctionne hors Internet, sur le réseau local du lycée.
- Conçu pour un Raspberry Pi derrière l'écran, branché en HDMI, avec Chromium en mode kiosque.
- **Statistiques d'activité** : élèves reçus, durée moyenne des consultations, taux d'occupation du cabinet, usage de chaque état ; graphiques, export CSV et PDF.
- **Réutilisable par n'importe quel établissement** : nom, titre, logo et infirmières se règlent dans `config.json`.

**Nouveau sur le projet ? Suivez le [guide pas à pas](docs/GUIDE.md)** : carte SD, réseau, SSH, installation, kiosque, mise en service.
**Autre établissement ? Voir [Adapter la solution](docs/ADAPTER.md).**

![Écran d'affichage](docs/img/affichage-2-consultation-pause.png)

<img src="docs/img/admin-3-absence-temporaire.png" alt="Page de commande sur téléphone" width="320">

## États

| État | Couleur | Affichage |
|---|---|---|
| Présente | vert | PRÉSENTE |
| En consultation | bleu | EN CONSULTATION |
| Absente temporairement | orange | ABSENTE TEMPORAIREMENT, avec « Retour vers 14h30 » (facultatif) |
| Absente (toute la journée) | rouge | ABSENTE |

**À l'heure de fin de ses horaires, l'infirmière repasse automatiquement en « Absente »** (une seule fois : si elle se remet « Présente » après son heure de fin, son choix est respecté ; si elle allonge ses horaires, la fermeture est réarmée). Chaque nuit (changement de date), tout le monde repasse aussi en « Absente » avec les
horaires par défaut, pour éviter un « Présente » oublié.

## Architecture

```
Téléphone / PC infirmière ──► /admin ──POST /api/set──┐
                                                       ▼
                                     server.py (Python)
                                       ├─ état courant : state.json
                                       ├─ journal des changements : data.db (SQLite)
                                       │        └──► /stats  (tableau de bord, CSV, PDF)
                                       │ Server-Sent Events (/api/events)
                                       ▼
        Chromium kiosque sur le Pi (HDMI) ◄── /  (page d'affichage)
```

| Fichier | Rôle |
|---|---|
| `server.py` | Serveur HTTP + temps réel (SSE), validation, journal d'activité, statistiques |
| `config.json` | Établissement, infirmières, administration, horaires, codes hachés (à créer ; non versionné, droits 600) |
| `auth.py` | Comptes, codes hachés, sessions, limitation des essais |
| `deploy/pins.py`, `deploy/setup-https.sh`, `deploy/use-standard-ports.sh` | Codes d'accès ; HTTPS ; ports 80/443 |
| `config.example.json` | Modèle de configuration |
| `branding/` | Logo de l'établissement (non versionné) |
| `static/index.html` | Page d'affichage de l'écran (s'adapte à toute taille d'écran et à 1 à 4 infirmières) |
| `static/admin.html` | Page de commande des infirmières (optimisée mobile) |
| `static/stats.html` | Tableau de bord des statistiques |
| `static/common.js` | Statuts, couleurs et fonctions partagées |
| `static/logo-default.svg` | Logo affiché tant qu'aucun logo n'est fourni |
| `deploy/install.sh` | Installation sur le Pi : service systemd + kiosque |
| `data.db`, `state.json` | Données d'activité et état courant (créés automatiquement ; non versionnés) |

## Installation sur le Raspberry Pi

Prérequis : Raspberry Pi OS (bureau, avec Chromium), connecté au réseau, avec
l'accès SSH. Testé sur Raspberry Pi OS basé sur Debian 13 (trixie), Python 3.13.
Aucun `sudo` n'est nécessaire pour l'application.

1. Copier le dossier sur le Pi, par exemple dans `~/cabinet` :
   ```bash
   scp -r cabinet infirmerie@<IP_DU_PI>:/home/infirmerie/
   ```
2. Créer la configuration :
   ```bash
   cd ~/cabinet && cp config.example.json config.json
   nano config.json
   ```
3. Installer le démarrage automatique :
   ```bash
   sh deploy/install.sh
   ```
4. Redémarrer le Pi : `sudo reboot`. Le serveur démarre, puis Chromium s'ouvre en plein écran.

`install.sh` crée :
- `~/.config/systemd/user/cabinet.service` : le serveur, relancé automatiquement s'il plante ;
- `~/.config/labwc/autostart` : lance `deploy/kiosk.sh` (Chromium en plein écran, relancé s'il se ferme).
  Attention : ce fichier remplace l'autostart par défaut de labwc, donc il n'y a ni barre des tâches ni bureau. C'est voulu pour un kiosque.

### Régler l'écran

- **Veille / extinction de l'écran** : `sudo raspi-config` → *Display Options* → *Screen Blanking* → désactiver.
- **Adresse IP fixe** : demander une réservation DHCP pour le Pi au service informatique, pour que l'adresse ne change pas.
- **Accès à distance (VNC)** : `sh deploy/setup-vnc.sh` active le bureau à distance (chiffré, avec mot de passe) sur le port 5900 (adresse à saisir dans le client : `<IP>` seule), pour dépanner depuis un autre PC. Voir la section 8.4 du [guide](docs/GUIDE.md).

## Utilisation par les infirmières

Ouvrir `https://pi-infirmerie-admin.local:8443/` sur le téléphone ou le PC (même réseau que le Pi) : ce **nom** remplace l'adresse IP, et le panneau a **son propre site**, séparé des statistiques (`pi-infirmerie-stats.local`) et de l'écran public (`pi-infirmerie.local`).

1. Toucher **son nom** puis saisir **son code personnel** ; l'infirmière ne voit que **son** panneau.
2. Toucher un des 4 boutons pour changer d'état. L'écran se met à jour en moins d'une seconde.
3. Pour une absence temporaire : choisir facultativement un retour dans 15 min, 30 min, 1 h, ou une heure précise.
4. Pour modifier les horaires du jour : déplier « Horaires d'aujourd'hui », puis *Enregistrer*.
   « Rétablir les horaires habituels » annule la modification.
5. **Se déconnecter** en bas de page sur un appareil partagé.

Liens personnels : `https://pi-infirmerie-admin.local:8443/?me=<id>` présélectionne le nom (le code reste obligatoire), pratique pour un QR code ou un favori.

Astuce : ajouter la page à l'écran d'accueil du téléphone (« Ajouter à l'écran d'accueil »). Avec `sh deploy/use-standard-ports.sh` (demande `sudo`), l'adresse devient simplement `https://pi-infirmerie.local`.

## Sécurité

Le Pi est joignable par tout le réseau du lycée, élèves compris. Les accès sont donc protégés :

| Qui | Accès |
|---|---|
| Tout le monde | L'écran d'affichage (présence uniquement, comme l'écran du couloir) |
| **Infirmière** | **Son** panneau et **ses** statistiques, avec **son code personnel** |
| **Administration** | Les statistiques de **toutes** les infirmières, en **lecture seule**, avec son code |

Codes **hachés** (scrypt), **HTTPS** obligatoire pour les pages protégées, **blocage** après des essais ratés, sessions limitées, **journal** des connexions, protection contre les requêtes forgées (CSRF). **Rien n'est ouvert tant qu'un compte n'a pas de code.**

```bash
sh deploy/setup-https.sh                  # chiffrement (autorité locale + certificat)
python3 deploy/pins.py generate           # un code pour chaque compte (affichés une fois)
systemctl --user restart cabinet.service
```
Détail, installation du certificat sur les appareils, pare-feu, codes oubliés : **[docs/SECURITE.md](docs/SECURITE.md)**.

**Accès depuis Internet** (un seul port HTTPS, second facteur obligatoire, rien de lisible sans connexion, limites de connexions, vrai certificat, règles de pare-feu) : **[docs/INTERNET.md](docs/INTERNET.md)**. Un vérificateur à lancer depuis l'extérieur : `python3 tools/check-exposure.py <adresse> <port>`.

## Configuration (`config.json`)

```json
{
  "school": "Lycée Exemple",
  "title": "Cabinet d'infirmières",
  "logo": "branding/logo.png",
  "port": 8080,
  "domain": "pi-infirmerie.local",
  "minEventSeconds": 10,
  "sessionHours": { "nurse": 12, "admin": 2 },
  "admins": [
    { "id": "administration", "name": "Administration" }
  ],
  "nurses": [
    { "id": "dupont", "name": "Mme Dupont",
      "hours": { "default": ["08:00", "18:00"], "wed": ["08:00", "12:00"] } }
  ]
}
```

- `school` : nom de l'établissement. `title` : titre affiché au centre de l'écran. `logo` : chemin du logo (PNG, JPG ou SVG) ; sans fichier, un logo par défaut s'affiche.
- `minEventSeconds` : les changements d'état plus courts (appui par erreur) sont ignorés dans les statistiques.
- `nurses` : une entrée par infirmière, une colonne par entrée sur l'écran. `id` : identifiant court sans espace, **à ne pas changer en cours d'année** (les statistiques y sont rattachées).
- `hours.default` : horaires habituels ; une clé `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` remplace les horaires pour ce jour.
- `admins` : comptes **administration** (statistiques de toutes, lecture seule). `sessionHours` : durée de connexion sans activité (infirmières 12 h, administration 2 h par défaut).
- **Les codes ne s'écrivent pas dans ce fichier** : ils se définissent avec `python3 deploy/pins.py` (stockés hachés). Un bloc `tls` est ajouté par `deploy/setup-https.sh`.
- Après modification : `systemctl --user restart cabinet.service`.

## Exploitation et dépannage

```bash
systemctl --user status cabinet.service     # état du serveur
journalctl --user -u cabinet.service -f     # journaux
systemctl --user restart cabinet.service    # redémarrer le serveur
curl localhost:8080/api/state               # état courant (JSON)
```

| Problème | Piste |
|---|---|
| Écran noir ou vide au démarrage | Vérifier le service (ci-dessus), puis lancer `~/cabinet/deploy/kiosk.sh` depuis une session du bureau |
| Pastille orange en bas à droite de l'écran | La connexion au serveur est perdue ; la page se recharge toute seule au retour |
| `/admin` injoignable depuis le téléphone | Le téléphone doit être sur le même réseau que le Pi (pas en 4G) ; vérifier le VLAN / Wi-Fi invité |
| Les changements n'apparaissent pas | Recharger la page ; vérifier `curl localhost:8080/api/state` sur le Pi |
| Code demandé en boucle | Le code saisi est faux ; recharger la page et ressaisir |

L'état courant est dans `state.json` (non versionné). Pour tout remettre à zéro : supprimer ce fichier puis redémarrer le service.

## API

| Route | Description |
|---|---|
| `GET /` | Page d'affichage |
| `GET /admin` | Page de commande (HTTPS ; écran de connexion si pas de session) |
| `GET /api/state` | État courant (JSON), **public** |
| `GET /api/events` | Flux temps réel (Server-Sent Events), **public** |
| `GET /logo`, `GET /ca.crt` | Logo de l'établissement ; autorité de certification locale |
| `GET /api/accounts` | Noms des comptes pour l'écran de connexion (jamais de code) |
| `POST /api/login` | `{"role":"nurse"\|"admin","id":"…","pin":"…"}` → cookie de session (HTTPS uniquement) |
| `POST /api/logout`, `GET /api/me` | Fermer la session ; compte connecté |
| `POST /api/set` | **Infirmière connectée**, pour son panneau : `{"status":"pause","back":"14:30"}` ; ou `{"start","end"}` pour les horaires ; ou `{"resetHours":true}` |
| `GET /stats` | Tableau de bord (connexion requise) ; `/stats?nurse=<id>` s'ouvre sur une infirmière |
| `GET /api/stats?from=AAAA-MM-JJ&to=AAAA-MM-JJ[&nurse=<id>]` | Statistiques (JSON) : administration = toutes, infirmière = les siennes |
| `GET /api/stats.csv`, `GET /api/events.csv` (mêmes paramètres) | Exports CSV, mêmes droits |

`status` : `present`, `consult`, `pause`, `absent`. `back` n'est accepté que pour `pause`. Les heures sont au format `HH:MM`. `"next": true` avec `"status":"consult"` ouvre une nouvelle consultation (élève suivant). Les routes protégées répondent **401** sans session, **403** si le compte n'a pas le droit (ou si l'accès se fait en HTTP), **429** pendant un blocage. Le serveur n'accepte aucun code dans une requête autre que la connexion.

## Limites connues

- Conçu pour un **réseau interne** : ne pas l'exposer sur Internet. Pas de double authentification (le code est le seul facteur). Les ports SSH et VNC du Pi doivent être limités aux postes de l'informatique ([docs/SECURITE.md](docs/SECURITE.md), section 7).
- Les statistiques mesurent ce que les infirmières déclarent avec les boutons : une consultation oubliée ouverte ou un appui manquant fausse les chiffres. Elles ne remplacent pas un registre officiel.
- Les mesures commencent au déploiement de la fonction de statistiques : pas d'historique antérieur.
- Au-delà de 4 infirmières sur un même écran, les textes deviennent petits.
- Les horaires sont par jour de la semaine ; pas de calendrier (vacances, jours fériés).

## Licence

MIT, voir [LICENSE](LICENSE).
