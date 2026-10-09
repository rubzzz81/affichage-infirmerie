# Guide pas à pas : de la carte SD à l'écran en service

Ce guide refait toute la mise en place, dans l'ordre. Chaque étape se termine par
une **vérification** : ne passez à la suivante que si elle est bonne.

Les commandes `PowerShell` se tapent sur le PC Windows, les commandes `bash` sur le
Raspberry Pi (après connexion SSH, étape 5).

## Sommaire

1. [Matériel](#1-matériel)
2. [Préparer la carte SD](#2-préparer-la-carte-sd)
3. [Brancher et démarrer](#3-brancher-et-démarrer)
4. [Trouver l'adresse IP du Pi](#4-trouver-ladresse-ip-du-pi)
5. [Se connecter en SSH](#5-se-connecter-en-ssh)
6. [Installer l'application](#6-installer-lapplication)
7. [Vérifier le kiosque et redémarrer](#7-vérifier-le-kiosque-et-redémarrer)
8. [Réglages de l'écran et du réseau](#8-réglages-de-lécran-et-du-réseau)
9. [Donner l'accès aux infirmières](#9-donner-laccès-aux-infirmières)
10. [Mettre à jour l'application](#10-mettre-à-jour-lapplication)
11. [Annexes](#11-annexes)

---

## 1. Matériel

- Un Raspberry Pi (testé sur un modèle 64 bits avec 2 Go de RAM), son alimentation officielle.
- Une carte microSD de 16 Go ou plus (de bonne marque), et un lecteur de cartes pour le PC.
- Un câble HDMI (ou micro-HDMI selon le modèle) vers l'écran.
- Un câble réseau RJ-45 vers un port du switch (le Wi-Fi n'est pas nécessaire).
- Un PC Windows avec [Raspberry Pi Imager](https://www.raspberrypi.com/software/).

## 2. Préparer la carte SD

1. Insérer la carte dans le lecteur du PC. Elle sera **entièrement effacée**.
2. Lancer **Raspberry Pi Imager** et choisir :
   - **Appareil** : votre modèle de Raspberry Pi ;
   - **Système** : *Raspberry Pi OS (64-bit)*, la version **avec bureau** (pas « Lite », car Chromium en kiosque est nécessaire) ;
   - **Stockage** : la carte SD. Vérifier la taille pour ne pas choisir un autre disque.
3. Dans la **personnalisation**, renseigner :
   - **Nom d'hôte** : par exemple `pi-infirmerie` ;
   - **Localisation** : fuseau horaire `Europe/Paris`, clavier `fr` ;
   - **Compte utilisateur** : un identifiant et un mot de passe. Les noter dans un endroit sûr (gestionnaire de mots de passe). Ne jamais les écrire dans le dépôt Git ;
   - **SSH activé** : oui (authentification par mot de passe, ou par clé publique si vous en avez une).
4. Cliquer sur **Écrire** et attendre la fin de l'écriture et de la vérification.

**Vérification** : Pi Imager affiche « Terminé ». Retirer la carte proprement.

### Si l'écriture échoue

Erreur « Error writing to storage device » : la cause est presque toujours la carte, le lecteur ou l'état du disque. Essayer, dans l'ordre :

1. Autre port USB (en direct sur le PC, pas sur un hub) ou autre lecteur de cartes.
2. Vérifier le verrou physique de l'adaptateur SD (position déverrouillée).
3. Lancer Pi Imager **en administrateur** et désactiver temporairement l'antivirus ou l'« Accès contrôlé aux dossiers » de Windows Defender.
4. Nettoyer la carte avec `diskpart` (invite de commandes **en administrateur**). **Vérifiez bien le numéro de disque par sa taille : cette commande efface le disque choisi.**
   ```
   diskpart
   list disk
   select disk X
   attributes disk clear readonly
   clean
   create partition primary
   format fs=fat32 quick
   exit
   ```
5. Reformater avec l'outil officiel **SD Memory Card Formatter** (option « Overwrite format »).
6. Tester la carte avec **H2testw** ou **F3** (fausse capacité ou carte défectueuse), ou essayer une autre carte.
7. En dernier recours, tenter une version précédente de Pi Imager (1.9.x) ou balenaEtcher.

## 3. Brancher et démarrer

1. Insérer la carte SD dans le Pi.
2. Brancher dans cet ordre : câble **HDMI** vers l'écran, câble **réseau** vers le switch, puis l'**alimentation**.
3. Attendre 1 à 2 minutes. Au premier démarrage, le Pi applique la personnalisation et peut redémarrer seul une fois.

**Vérification** : le bureau de Raspberry Pi OS apparaît sur l'écran.

## 4. Trouver l'adresse IP du Pi

Le PC doit être sur le même réseau que le Pi.

**Méthode simple** : depuis le PC.
```powershell
ping pi-infirmerie.local
```
Si le nom ne répond pas, utiliser l'une des méthodes suivantes.

**Depuis le Pi** : ouvrir un terminal sur son bureau et taper `hostname -I`.

**Depuis le switch Aruba 2530** (voir [l'annexe A](#a-console-du-switch-aruba-2530)) :
```
enable
show arp
show mac-address
```
Chercher l'adresse correspondant au port où le Pi est branché.

**Vérification** : `ping <IP>` répond.
```powershell
ping 10.5.35.101
```
(Dans notre installation, l'adresse est `10.5.35.101`. Remplacez-la par la vôtre dans la suite.)

## 5. Se connecter en SSH

1. Tester que le service SSH répond :
   ```powershell
   Test-NetConnection <IP> -Port 22
   ```
   `TcpTestSucceeded : True` signifie que le port est ouvert.
2. Se connecter :
   ```powershell
   ssh <utilisateur>@<IP>
   ```
   Au premier accès, répondre `yes` à la question sur l'empreinte. Saisir le mot de passe (rien ne s'affiche à la frappe, c'est normal).

**Vérification** : l'invite du Pi apparaît (`utilisateur@pi-infirmerie:~ $`).

### Confort : connexion par clé (facultatif)

Évite de retaper le mot de passe. Sur le PC :
```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\id_ed25519_pi -N '""'
type $env:USERPROFILE\.ssh\id_ed25519_pi.pub | ssh <utilisateur>@<IP> "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
ssh -i $env:USERPROFILE\.ssh\id_ed25519_pi <utilisateur>@<IP> hostname
```
Utiliser une clé dédiée à ce Pi. Pour retirer un accès, supprimer la ligne correspondante dans `~/.ssh/authorized_keys` sur le Pi.

Erreurs fréquentes :

| Message | Cause probable |
|---|---|
| `Connection timed out` | Le Pi n'a pas démarré, mauvaise IP, ou PC et Pi pas sur le même réseau |
| `Connection refused` | SSH non activé (le réglage de Pi Imager n'a pas été appliqué) |
| `Permission denied` | Mot de passe ou identifiant faux (vérifier la casse et AZERTY/QWERTY) |

## 6. Installer l'application

1. Récupérer le dépôt sur le PC (`git clone <URL_DU_DEPOT>`), puis copier le dossier sur le Pi :
   ```powershell
   scp -r cabinet <utilisateur>@<IP>:/home/<utilisateur>/
   ```
2. Se connecter en SSH et créer la configuration :
   ```bash
   cd ~/cabinet
   cp config.example.json config.json
   nano config.json
   ```
   Renseigner les infirmières et leurs horaires (voir la section *Configuration* du [README](../README.md)). Enregistrer avec `Ctrl+O`, `Entrée`, puis quitter avec `Ctrl+X`.
3. Vérifier que Python est présent (il l'est par défaut sur Raspberry Pi OS) :
   ```bash
   python3 --version
   ```
4. Lancer l'installation :
   ```bash
   sh deploy/install.sh
   ```
   Ce script crée le service du serveur et le démarrage automatique du kiosque (sans `sudo`).
5. Contrôler le serveur :
   ```bash
   systemctl --user status cabinet.service
   curl localhost:8080/api/state
   ```

**Vérification** : le service est `active (running)` et `curl` renvoie du JSON avec vos infirmières.

## 7. Vérifier le kiosque et redémarrer

1. Redémarrer le Pi :
   ```bash
   sudo reboot
   ```
2. Après environ une minute, l'écran doit afficher **le logo, l'heure et une carte par infirmière en rouge « Absente »**, sans barre ni bureau.

**Vérification** : l'affichage apparaît tout seul, sans intervention.

Si l'écran reste noir ou affiche le bureau :
```bash
systemctl --user status cabinet.service      # le serveur tourne-t-il ?
cat ~/.config/labwc/autostart                # le kiosque est-il déclaré ?
~/cabinet/deploy/kiosk.sh                    # lancer le kiosque à la main depuis le bureau
```

## 8. Réglages de l'écran et du réseau

1. **Désactiver la mise en veille de l'écran** : `sudo raspi-config` → *Display Options* → *Screen Blanking* → désactiver.
2. **Adresse IP fixe** : demander au service informatique une **réservation DHCP** pour l'adresse MAC du Pi (`ip link show eth0`), pour que l'adresse ne change jamais.
3. **Code d'accès** (recommandé) : renseigner `"pin": "1234"` (votre code) dans `config.json`, puis :
   ```bash
   systemctl --user restart cabinet.service
   ```
   Sans code, toute personne du réseau du lycée qui connaît l'adresse `/admin` peut changer l'affichage.

## 9. Donner l'accès aux infirmières

1. Donner l'adresse : `http://<IP>:8080/admin` (ou un QR code de cette adresse).
2. Sur son téléphone (réseau du lycée) : ouvrir l'adresse, toucher son nom (une seule fois).
3. Conseiller « Ajouter à l'écran d'accueil » pour y accéder en un geste.
4. Mode d'emploi en une phrase : **toucher le bouton qui correspond à sa situation ; l'écran se met à jour tout de suite.**

| Bouton | Quand l'utiliser |
|---|---|
| Présente | Je suis au cabinet |
| Consultation | Je reçois un élève |
| Absente un instant | Je m'absente un moment (indiquer un retour si possible) |
| Absente | Je ne suis pas là |

À l'heure de fin de ses horaires, l'état passe tout seul en « Absente ».

**Vérification** : changer un état depuis un téléphone, l'écran change en moins d'une seconde.

## 10. Mettre à jour l'application

Depuis le PC, après modification du code :
```powershell
scp -r cabinet\server.py cabinet\static <utilisateur>@<IP>:/home/<utilisateur>/cabinet/
ssh <utilisateur>@<IP> "systemctl --user restart cabinet.service"
```
Pour une modification de la page d'affichage seulement, `scp` du fichier suffit, puis recharger l'écran (redémarrer le Pi ou fermer/relancer Chromium).

Pour modifier la configuration (noms, horaires) : éditer `~/cabinet/config.json` sur le Pi, puis redémarrer le service.

## 11. Annexes

### A. Console du switch Aruba 2530

Utile pour trouver l'IP du Pi ou régler le réseau.

1. Brancher un câble micro-USB **de données** sur le port console du switch (à l'avant).
2. Dans le Gestionnaire de périphériques, repérer le port `COMx` sous *Ports (COM et LPT)*. S'il manque, installer le pilote **Silicon Labs CP210x**.
3. Ouvrir PuTTY ou Tera Term : *Serial*, port `COMx`, **9600** bauds, 8 bits, pas de parité, 1 bit d'arrêt, **aucun** contrôle de flux. Appuyer sur `Entrée`.
4. Commandes utiles :
   ```
   enable
   show ip                  # adresses IP du switch
   show arp                 # machines vues sur le réseau
   show mac-address         # port auquel chaque machine est branchée
   configure
   vlan 1
   ip address 192.168.1.10 255.255.255.0
   exit
   telnet-server            # activer Telnet (préférer SSH si possible)
   password manager         # définir un mot de passe
   write memory             # enregistrer la configuration
   ```
5. Mot de passe perdu : bouton **Clear** en façade (efface le mot de passe). Le bouton **Reset** redémarre le switch ; une réinitialisation d'usine efface toute la configuration.

### B. Commandes d'exploitation

```bash
systemctl --user status cabinet.service     # état du serveur
journalctl --user -u cabinet.service -f     # journaux en direct
systemctl --user restart cabinet.service    # redémarrer le serveur
curl localhost:8080/api/state               # état courant
```
Remettre l'état à zéro : arrêter le service, supprimer `~/cabinet/state.json`, relancer le service.

### C. Publier le dépôt sur GitHub

```bash
gh auth login
cd cabinet
gh repo create affichage-infirmerie --private --source=. --push
```
Le fichier `config.json` (noms du personnel) et `state.json` sont exclus par `.gitignore` et ne sont jamais publiés. Ne jamais commiter de mot de passe, de code d'accès ni de clé privée.

### D. Sécurité en bref

- L'application est prévue pour le **réseau interne** du lycée, en HTTP : ne pas l'exposer sur Internet.
- Utiliser le `pin` pour limiter les modifications.
- Garder les identifiants du Pi hors du dépôt et hors des messages partagés.
