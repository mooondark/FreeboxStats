# Dashboard web Freebox — design

## Contexte

`fbxstat.py` affiche déjà en temps réel dans le terminal : vitesse WAN, IPv4/IPv6,
uptime, températures, ventilateur, statut des services (Internet/Authentification/
Téléphone), tableau des ports switch actifs, tableau des appareils connectés.

Objectif : reprendre ces mêmes informations dans un dashboard web graphique,
consultable depuis un navigateur, avec historique glissant sur les métriques qui
varient dans le temps (vitesse WAN, températures, ventilateur).

## Périmètre

- Un nouveau serveur web local (aucune exposition externe, aucune authentification
  utilisateur — même modèle de confiance que `fbxstat.py`, réseau local uniquement).
- `fbxstat.py` (terminal) n'est pas modifié ni remplacé.
- La logique d'authentification Freebox (dupliquée dans `fbxstat.py`,
  `freebox_wan_`, `freebox_temp_`) est extraite dans un module partagé
  `freebox_api.py`, réutilisé par le nouveau serveur.

## Architecture

```
freebox_api.py      -- auth Freebox (app_token, session), fonctions get_app_token/open_session
fbxstat_web.py       -- serveur HTTP + thread de collecte
templates/dashboard.html -- page unique (HTML+CSS+JS, Chart.js via CDN)
```

### `freebox_api.py`

Extrait de `fbxstat.py` : `APP_ID`, `TOKEN_FILE` (`~/.fbxstat_token.json`, même
app_id `fr.fbxstat.app` que `fbxstat.py` — réutilise le token déjà autorisé, pas
de nouvelle validation sur l'écran de la Freebox), `register_app()`,
`get_app_token()`, `open_session()`. `fbxstat.py` est mis à jour pour importer ce
module au lieu de dupliquer le code (nettoyage, aucun changement de comportement).

### `fbxstat_web.py`

- **Intervalle de rafraîchissement configurable** : argument CLI `--interval`
  (secondes, défaut `1`). Contrôle à la fois la fréquence du thread de collecte
  et la fréquence de polling du frontend (exposée au frontend via
  `/api/snapshot`, champ `interval`, lu une fois au chargement de la page pour
  régler les `setInterval`).
- **Thread de collecte** (`collect_loop`) : toutes les `interval` secondes,
  appelle `/connection/`, `/system/`, `/switch/status/` +
  `/switch/port/{id}/stats/` pour les ports actifs, `/lan/browser/pub/`,
  `/phone/` — même séquence que `fbxstat.py`.
- **Historique en mémoire** : un `collections.deque(maxlen=600/interval)` (fenêtre
  fixe de 10 minutes, quel que soit l'intervalle choisi) de tuples
  `(timestamp, rate_down, rate_up, sensors, fan_rpm)`. Perdu au redémarrage du
  serveur — acceptable pour du monitoring temps réel, pas de persistance
  demandée.
- **Verrou** (`threading.Lock`) autour de l'état partagé (historique + dernier
  snapshot) entre le thread de collecte et le thread HTTP.
- **Serveur HTTP** (`http.server.ThreadingHTTPServer` + `BaseHTTPRequestHandler`) :
  - `GET /` → sert `templates/dashboard.html`.
  - `GET /api/history` → JSON des 600 points (séries temporelles pour les 2
    graphiques).
  - `GET /api/snapshot` → JSON du dernier point : statuts (Internet/Auth/Téléphone),
    matériel/firmware, IPv4/IPv6, uptime, tableau ports, tableau appareils (même
    contenu que les lignes/tableaux de `fbxstat.py`, en JSON plutôt qu'en texte
    aligné).

### `templates/dashboard.html`

- Thème sombre, cartes, proche de la capture fournie.
- **Graphique 1** : vitesse WAN down/up sur les 10 dernières minutes (Chart.js,
  un axe Y, deux séries).
- **Graphique 2** : températures (axe Y gauche, une série par sonde) + RPM
  ventilateur (axe Y droit, `yAxisID` secondaire Chart.js).
- **Tableau ports** : port, vitesse (mapping 10/100/1000/2500/10000 → 10M-10G),
  débit down/up — uniquement les ports avec lien actif.
- **Tableau appareils** : nom, IPv4, IPv6 locale, type, constructeur — uniquement
  les appareils avec IPv4 active, triés par IPv4 croissante.
- **Bandeau d'en-tête** : matériel, version FreeboxOS, statuts Internet/
  Authentification/Téléphone (vert/rouge), IPv4/IPv6, uptime.
- Mise à jour via `fetch()` + `setInterval`, cadencée sur l'`interval` renvoyé
  par `/api/snapshot` (deux appels distincts par tick, `/api/snapshot` et
  `/api/history`, chacun JSON léger). Pas de WebSocket : le polling est
  largement suffisant à ces fréquences.

## Erreurs

- Session expirée (`auth_required`) : le thread de collecte relance
  `open_session()` comme le fait déjà `fbxstat.py`.
- Un appel API échoue ponctuellement (ex: `/phone/` indisponible) : le point
  correspondant est marqué manquant dans le snapshot plutôt que de faire
  planter le thread de collecte (`try/except` par appel, log stderr).

## Hors périmètre

- Pas d'authentification utilisateur sur le dashboard web (réseau local de
  confiance, comme les scripts existants).
- Pas de persistance de l'historique au-delà de la mémoire du process.
- Pas de HTTPS/TLS.
