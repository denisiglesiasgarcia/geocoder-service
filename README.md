# geocoder-service

→→Géocodeur d'adresses du canton de Genève, basé sur les données ouvertes SITG
(`CAD_ADRESSE`) et Meilisearch. Alternative auto-hébergée à l'API SITG Lab
(`geocodage.sitg-lab.ch`).

## Déploiement local (Docker)

```bash
cd geocoder-service
cp .env.example .env   # puis changer MEILI_MASTER_KEY

docker compose --env-file .env up -d
```

`MEILI_MASTER_KEY` doit faire au moins 16 octets (exigence Meilisearch : "Use
a secure, randomly generated string"). Générer une valeur adaptée :

```bash
openssl rand -base64 32
# ou, sans openssl :
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copier le résultat dans `.env` à la place de la valeur d'exemple.

Le script `entrypoint.sh` va effectuer plusieurs étapes lors de la création des containers:
1) Créer le container Meilisearch et attendre qu'il réponde
2) Créer le container `api` (l'API FastAPI)
  a) Télécharger les données SITG (`CAD_ADRESSE`) avec le script `download.py`
  b) Envoyer et indexer les données dans Meilisearch avec le script `ingest.py`

```bash
curl "http://localhost:8000/search?q=Av.%20J-D%20Maillard,%207&limit=3"
curl "http://localhost:8000/health"
```

### Compatible avec [`sitg-geocode`](https://github.com/denisiglesiasgarcia/sitg-geocode)

Endpoint supplémentaire, `/api/v2/search`, compatible avec le format de
réponse de l'API SITG Lab v2 (`geocodage.sitg-lab.ch/api/v2/search`) — un
client écrit contre cette API (ex.
[`sitg-geocode`](https://github.com/denisiglesiasgarcia/sitg-geocode)) peut
pointer sur ce service à la place en changeant uniquement l'URL de base, sans
aucun changement de code (voir `sitg_compat.py`) :

```bash
curl "http://localhost:8000/api/v2/search?q=Av.%20J-D%20Maillard,%207&limit=3"
```

Pour forcer une réindexation (ex. après une mise à jour des données SITG) :

```bash
docker compose exec api python -m geocoder_service.ingest --force
```

## Utilisation

Directement en Python :

```python
from geocoder_service.search import geocode

geocode("Av. de Thonex 30", limit=3)
# -> liste de hits triés par score décroissant (0-100), ex. :
# [{"adresse": "Avenue de Thônex 30", "commune": "Chêne-Bourg", "score": 100.0, ...}, ...]
```

Ou via l'API HTTP (`GET /search?q=...&limit=...`, `GET /api/v2/search?...`,
`GET /health`) une fois le service démarré — l'API est entièrement
asynchrone (FastAPI + `httpx`), voir `geocode_async()` pour l'équivalent
asynchrone de `geocode()` (utilisé en interne par l'API, mais aussi
directement utilisable, ex. avec `asyncio.gather` pour géocoder beaucoup
d'adresses en parallèle sans bloquer sur chaque appel réseau).

Voir aussi [`notebooks/examples.ipynb`](notebooks/examples.ipynb) pour des exemples exécutables
(abréviations, initiales ambiguës, suffixes bis/ter, plages de numéros, API
HTTP, comparaison sur le jeu de test). Nécessite `uv sync` (dépendance de dev
`ipykernel`) et la stack Docker démarrée.

## Comment ça marche

- **Données** : `CAD_ADRESSE-SHP.zip` (open data SITG), ré-extraites à la demande.
  Le fichier `.cpg` indique un encodage UTF-8 — c'est déjà géré dans `ingest.py`.
- **Synonymes/abréviations** : construits automatiquement à partir des couples
  `TYPABR`/`TYVOIE` réellement présents dans les données (`ch.`↔`Chemin`,
  `av.`↔`Avenue`, etc.) et des initiales de prénoms composés du champ `LIANT`
  (ex. "j-d" ↔ "Jacob-Daniel", pour matcher "Av. J-D Maillard"). Persistés dans
  `data/abbr_to_full.json` (`abréviation -> liste de formes complètes`) et
  réutilisés à la fois pour les synonymes Meilisearch et pour le score. Les
  mêmes initiales peuvent désigner plusieurs prénoms distincts (ex. "j-d" =
  "Jean-Daniel" OU "Jacob-Daniel") : les deux sont gardés, et c'est le reste de
  l'adresse (nom de rue, numéro) qui départage via le score.
- **Score (0-100)** : calculé côté client (`score.py`), pas seulement le
  `_rankingScore` interne de Meilisearch (trop grossier). Deux signaux :
  similarité textuelle (`rapidfuzz.token_set_ratio`, sur adresse et
  adresse+localité, en essayant toutes les interprétations d'une abréviation
  ambiguë, en gardant le maximum) + bonus/malus sur la concordance exacte du
  numéro de rue (une plage en fin de requête comme "51-53" accepte l'une ou
  l'autre borne, les deux existant souvent comme adresses distinctes).
- **`ingest.py` vérifie que le nombre de documents indexés correspond au nombre
  d'enregistrements source** et fait échouer l'ingestion sinon. Une ingestion
  précédente avait silencieusement perdu ~18% des adresses (deux lots de 5000
  rejetés par Meilisearch à cause d'IDPADR avec un espace parasite) sans qu'aucune
  erreur ne remonte — d'où ce garde-fou explicite plutôt qu'un simple print.
- **Suffixes de numéro** ("bis"/"ter", vérifiés sur 740 + 82 adresses réelles) :
  "7b" est reconnu comme équivalent de "7bis" pour la concordance de numéro.
  Un numéro suivi d'une lettre isolée par un espace ("14 A") est aussi
  reconnu comme équivalent de "14A" (ancré en fin de requête, pour ne pas
  confondre avec un numéro placé en tête, ex. "12 rue Jean-Charles Amat").
- **Codes d'entrée cadastraux** (ex. "0 E01", "O03") : un placeholder "0"
  suivi d'une lettre et de 1-2 chiffres, présent dans certains exports SITG
  pour désigner une entrée de bâtiment sans numéro de rue propre. "0"
  n'existe comme numéro de rue nulle part dans `CAD_ADRESSE` : traité comme
  absence de numéro (pas de bonus/malus) plutôt que comme un vrai numéro, et
  ces codes sont retirés du texte avant le calcul de similarité pour ne pas
  le polluer avec des tokens que l'adresse officielle ne contiendra jamais.
- **Abréviations manuelles supplémentaires**, non couvertes par les données
  SITG (`TYPABR`/`TYVOIE`) car pas des types de voie à proprement parler :
  `bvd`/`sq`/`tsse`/`car`/`ven`/`pt` (variantes courantes de
  boulevard/square/terrasse/carrefour/venelle/pont) et `st`/`ste` pour
  "Saint"/"Sainte" (préfixe très fréquent *à l'intérieur* des noms de rue,
  ex. "Rue St-Joseph", 836 adresses concernées).
- **Initiales composées au format "X.-Y."** (point et tiret combinés, ex.
  "J.-A.-GAUTIER", "F.-A.-GRISON") : la table d'abréviations générait bien
  des variantes "j-a"/"j.a"/"ja" à partir du champ `LIANT`, mais pas la
  combinaison point+tiret que ce format utilise réellement — plusieurs
  dizaines d'adresses concernées.
- **Mots-vides ("stopWords")** : les types de voie complets (ex. "chemin",
  "route") sont exclus du classement par pertinence Meilisearch, pour qu'un
  mot très fréquent (et parfois faux — l'utilisateur se trompe de type) ne
  l'emporte pas sur le nom de rue réel, plus rare et discriminant. Seules les
  formes complètes sont concernées, pas les abréviations : une abréviation qui
  est à la fois mot-vide et clé de synonyme empêchait Meilisearch de résoudre
  le synonyme pour cette requête. Les mots de liaison génériques ("de", "des",
  "la"...) ont été essayés puis retirés : certains noms de rue les utilisent
  comme partie intégrante d'un nom composé (ex. "Chemin De-Verey"), donc les
  neutraliser cassait plus de cas réels que ça n'en corrigeait.

Vérifié sur les 107 adresses de `tests/test_adresses.csv` : 95/107 avec un
score ≥ 95, contre 61/107 pour l'API SITG Lab en v2 au même seuil (voir
`compare.py`). Vérifié aussi sur un jeu plus large et volontairement
"bruité" (`tests/test_adresses_20260707_152332.csv`, ~130k adresses avec
abréviations, fautes de frappe, casse aléatoire, codes d'entrée cadastraux,
etc.) : 97.64% avec un score ≥ 95 (contre 94.87% avant les correctifs
ci-dessus).

## Sécurité

- Le serveur qui répond aux requêtes HTTP (`search.py`/`api.py`) n'utilise
  jamais `MEILI_MASTER_KEY` (accès admin complet : suppression d'index,
  gestion des clés...) pour ses requêtes de recherche : `ingest.py` écrit au
  démarrage la clé Meilisearch "Default Search API Key" (action `search`
  uniquement, créée automatiquement par Meilisearch) dans
  `data/search_api_key.txt`, relue par `search.py`. `MEILI_MASTER_KEY` reste
  nécessaire au conteneur `api` pour l'étape d'ingestion elle-même (même
  conteneur, avant le lancement d'uvicorn) — pas encore séparée dans un
  service à part.
- Le port Meilisearch (`7700`) est lié à `127.0.0.1` uniquement dans
  `docker-compose.yml`, pas `0.0.0.0` : accessible pour le développement
  local de l'API hors Docker, mais pas depuis le réseau. Le conteneur `api`
  y accède via le réseau Docker interne (`meilisearch:7700`), indépendamment
  de ce port publié.

## Limites connues

- Les adresses avec plusieurs fautes de frappe cumulées sur un mot rare et peu
  discriminant (ex. "Jaous" pour "James") peuvent ne retourner aucun résultat
  ("Bvd Jaous Fazy 23") — testé aussi avec un algorithme phonétique dédié au
  français (FONEM, bibliothèque `abydos`), qui n'apporte qu'un gain marginal
  sur nos cas réels (ex. Bessonnette/Bassonette : 85.7 -> 88.9 seulement) et
  n'a donc pas été intégré.
- Scope volontairement limité au canton de Genève (pas de RegBL/BAN).

## Fichiers

- `docker-compose.yml` — Meilisearch + API (l'API attend Meilisearch puis
  s'auto-indexe au démarrage, voir `entrypoint.sh`), volume `geocoder_data`
  pour persister les données téléchargées, `abbr_to_full.json` et
  `search_api_key.txt`. Port Meilisearch lié à `127.0.0.1` (voir Sécurité).
- `Dockerfile` / `entrypoint.sh` — image de l'API : attend Meilisearch,
  télécharge/indexe si besoin (idempotent), puis lance uvicorn.
- `src/geocoder_service/download.py` — téléchargement/extraction des données (idempotent).
- `src/geocoder_service/ingest.py` — indexation Meilisearch + génération des synonymes
  (idempotent : ignore si l'index est déjà à jour, `--force` pour forcer) +
  écriture de la clé API `search`-only (voir Sécurité).
- `src/geocoder_service/score.py` — calcul du score de confiance.
- `src/geocoder_service/search.py` — points d'entrée `geocode()` (synchrone) et
  `geocode_async()` (asynchrone, `httpx`).
- `src/geocoder_service/api.py` — API HTTP (FastAPI, asynchrone) exposant
  `geocode_async()` sur `/search` et `/api/v2/search`.
- `src/geocoder_service/sitg_compat.py` — traduit les résultats au format de
  l'API SITG Lab v2, pour `/api/v2/search`.
- `src/geocoder_service/compare.py` — script de comparaison sur `tests/test_adresses.csv`.
