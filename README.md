# FbxStat

Outils de supervision pour **Freebox** (testé sur une Freebox v9 / Freebox OS 4.12) via l'[API Freebox OS](https://dev.freebox.fr/sdk/os/) :

- un **dashboard web** temps réel (débit WAN, températures, ventilateur, ports du switch, appareils connectés) ;
- un **moniteur terminal** équivalent ;
- deux **plugins Munin** (trafic WAN et températures).

## Aperçu

<img width="1919" height="1080" alt="image" src="https://github.com/user-attachments/assets/67db4c48-b760-40b1-91e1-2c83f6fdd68c" />

## Fonctionnalités

**Dashboard web** (`fbxstat_web.py`)

- Graphique du débit WAN descendant / montant, chacun sur son propre axe (Mb/s).
- Graphique des températures (axe de gauche, °C) et de la vitesse du ventilateur (axe de droite, RPM).
- Fenêtre glissante réglable : 1, 3 ou 5 minutes.
- Fréquence de rafraîchissement réglable depuis la page : 1, 3 ou 5 secondes.
- En-tête : matériel, version de Freebox OS, état des services (Internet, Authentification, Téléphone), IPv4/IPv6, uptime et débit global.
- Tableau des ports du switch avec lien actif (vitesse négociée, débit descendant/montant).
- Tableau des appareils connectés (nom, IPv4, IPv6 locale, IPv6 globale, type sous forme d'icône, constructeur), triés par IPv4. Quand un appareil a plusieurs IPv6, celle affichée est la dernière utilisée (`last_activity`), à défaut la première de la liste.
- Bandeau « DONNEES FIGEES » si les données ne sont plus mises à jour.

**Moniteur terminal** (`fbxstat.py`) : mêmes informations, affichées et rafraîchies dans le terminal.

**Plugins Munin** (dossier `munin plugins/`) : `freebox_wan_` (trafic WAN, catégorie *network*) et `freebox_temp_` (températures, catégorie *sensors*).

## Prérequis

- Python 3
- La bibliothèque [`requests`](https://pypi.org/project/requests/) : `pip install requests`
- Un accès réseau à la Freebox (`mafreebox.freebox.fr`)

## Installation et premier lancement

```bash
git clone https://github.com/mooondark/FreeboxStats.git
cd FreeboxStats
pip install requests
python fbxstat_web.py
```

Au **premier lancement**, l'application demande une autorisation à la Freebox : le message « Valide la demande sur l'ecran de la Freebox... » s'affiche, il faut alors valider la demande **sur l'écran de la Freebox**. Le jeton obtenu est ensuite enregistré dans `~/.fbxstat_token.json` et réutilisé automatiquement (le dashboard et le moniteur terminal partagent le même jeton).

## Utilisation

### Dashboard web

```bash
python fbxstat_web.py
```

Puis ouvre <http://127.0.0.1:8000>.

| Option       | Défaut      | Description                                                        |
|--------------|-------------|--------------------------------------------------------------------|
| `--interval` | `1`         | Secondes entre deux relevés (modifiable ensuite depuis la page)    |
| `--host`     | `127.0.0.1` | Adresse d'écoute (`0.0.0.0` pour rendre le dashboard visible du LAN) |
| `--port`     | `8000`      | Port d'écoute                                                      |

L'historique (600 relevés au maximum) est conservé en mémoire : il est perdu à l'arrêt du serveur.

> **Sécurité :** le dashboard n'a pas d'authentification et expose la liste de tes appareils. Par défaut il n'écoute que sur `127.0.0.1` ; n'utilise `--host 0.0.0.0` que sur un réseau de confiance.

### Moniteur terminal

```bash
python fbxstat.py
```

`Ctrl+C` pour quitter.

### Plugins Munin

Les plugins se trouvent dans `munin plugins/`. Sur le serveur Munin :

```bash
# Copier les plugins et les rendre exécutables
sudo cp "munin plugins/freebox_wan_" "munin plugins/freebox_temp_" /usr/share/munin/plugins/
sudo chmod +x /usr/share/munin/plugins/freebox_wan_ /usr/share/munin/plugins/freebox_temp_

# Activer les plugins
sudo ln -s /usr/share/munin/plugins/freebox_wan_  /etc/munin/plugins/freebox_wan
sudo ln -s /usr/share/munin/plugins/freebox_temp_ /etc/munin/plugins/freebox_temp

# Jeton Freebox (voir ci-dessous), lisible uniquement par l'utilisateur munin
sudo cp ~/.fbxstat_token.json /etc/munin/fbxstat_token.json
sudo chown munin:munin /etc/munin/fbxstat_token.json
sudo chmod 600 /etc/munin/fbxstat_token.json

sudo systemctl restart munin-node
```

Les plugins Munin lisent leur jeton dans `/etc/munin/fbxstat_token.json` (et non `~/.fbxstat_token.json`) car `munin-node` ne dispose pas toujours de la variable `HOME`. Le jeton doit donc être copié à cet endroit (il s'agit du même jeton que celui du dashboard : pas de nouvelle validation sur la Freebox).

Vérification :

```bash
sudo -u munin munin-run freebox_wan config
sudo -u munin munin-run freebox_wan
```

Le plugin `freebox_wan_` utilise des compteurs cumulatifs (`COUNTER`) comme le plugin standard `if_` : Munin calcule lui-même le débit.

## Organisation du code

| Fichier                  | Rôle                                                                 |
|--------------------------|----------------------------------------------------------------------|
| `fbxstat_web.py`         | Serveur web : collecte en arrière-plan, historique, API JSON         |
| `templates/dashboard.html` | Page du dashboard (Chart.js chargé depuis cdnjs)                   |
| `freebox_api.py`         | Authentification Freebox (jeton d'application, session)              |
| `freebox_data.py`        | Récupération et mise en forme des données (ports, appareils, statut) |
| `fbxstat.py`             | Moniteur terminal                                                    |
| `munin plugins/`         | Plugins Munin                                                        |
| `tests/`                 | Tests unitaires                                                      |

API exposée par le serveur :

- `GET /` : le dashboard
- `GET /api/snapshot` : dernier état complet
- `GET /api/history` : historique des relevés
- `POST /api/interval` : change l'intervalle de relevé (`{"interval": 1 | 3 | 5}`)

## Tests

```bash
python -m unittest discover -s tests
```

## Limites connues

- L'API Freebox n'expose pas de débit par appareil : seuls le débit global (WAN) et le débit par port du switch sont disponibles.
- Le dashboard charge Chart.js depuis un CDN : une connexion Internet est nécessaire pour afficher les graphiques.
