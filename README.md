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
| `config.json` | Établissement, infirmières, horaires, code d'accès, port (à créer ; non versionné) |
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

## Utilisation par les infirmières

Ouvrir `http://<IP_DU_PI>:8080/admin` sur le téléphone ou le PC (même réseau que le Pi).

1. À la première visite : toucher son nom (retenu ensuite sur l'appareil).
2. Toucher un des 4 boutons pour changer d'état. L'écran se met à jour en moins d'une seconde.
3. Pour une absence temporaire : choisir facultativement un retour dans 15 min, 30 min, 1 h, ou une heure précise.
4. Pour modifier les horaires du jour : déplier « Horaires d'aujourd'hui », puis *Enregistrer*.
   « Rétablir les horaires habituels » annule la modification.

Liens personnels : `http://<IP_DU_PI>:8080/admin?me=<id>` sélectionne directement l'infirmière (`id` du fichier de configuration), pratique pour un QR code ou un favori.

Astuce : ajouter la page à l'écran d'accueil du téléphone (« Ajouter à l'écran d'accueil »).
On peut aussi afficher un QR code vers cette adresse près de l'écran.

## Configuration (`config.json`)

```json
{
  "school": "Lycée Exemple",
  "title": "Cabinet d'infirmières",
  "logo": "branding/logo.png",
  "port": 8080,
  "pin": "",
  "minEventSeconds": 10,
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
- `pin` : code d'accès facultatif. S'il est défini, il est demandé une fois par appareil pour modifier un état.
  **Recommandé** : sans code, toute personne du réseau du lycée qui connaît `/admin` peut changer l'affichage.
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
| `GET /admin` | Page de commande |
| `GET /api/state` | État courant (JSON) |
| `GET /api/events` | Flux temps réel (Server-Sent Events) |
| `GET /stats` | Tableau de bord des statistiques |
| `GET /api/stats?from=AAAA-MM-JJ&to=AAAA-MM-JJ` | Statistiques de la période (JSON) |
| `GET /api/stats.csv`, `GET /api/events.csv` | Exports CSV : résumé par infirmière, détail de chaque changement d'état |
| `GET /logo` | Logo de l'établissement |
| `POST /api/set` | Modifier un état. Corps JSON : `{"id":"dupont","status":"pause","back":"14:30","pin":"…"}` ; ou `{"id","start","end"}` pour les horaires ; ou `{"id","resetHours":true}` |

`status` : `present`, `consult`, `pause`, `absent`. `back` n'est accepté que pour `pause`. Les heures sont au format `HH:MM`. `"next": true` avec `"status":"consult"` ouvre une nouvelle consultation (élève suivant). Les routes `/api/stats*` et `/api/events.csv` attendent le code d'accès dans l'en-tête `X-Pin` s'il est défini.

## Limites connues

- Pas d'authentification individuelle : un code partagé facultatif (`pin`), non chiffré (HTTP). Adapté à un réseau interne de confiance, pas à Internet.
- Les statistiques mesurent ce que les infirmières déclarent avec les boutons : une consultation oubliée ouverte ou un appui manquant fausse les chiffres. Elles ne remplacent pas un registre officiel.
- Les mesures commencent au déploiement de la fonction de statistiques : pas d'historique antérieur.
- Au-delà de 4 infirmières sur un même écran, les textes deviennent petits.
- Les horaires sont par jour de la semaine ; pas de calendrier (vacances, jours fériés).

## Licence

MIT, voir [LICENSE](LICENSE).
