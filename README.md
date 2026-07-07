# geocoder-service

Géocodeur d'adresses du canton de Genève, basé sur les données ouvertes SITG
(`CAD_ADRESSE`) et Meilisearch. Alternative auto-hébergée à l'API SITG Lab
(`geocodage.sitg-lab.ch`), scopée volontairement au canton de Genève.

## Déploiement local (Docker, tout inclus)

```bash
cd geocoder-service
cp .env.example .env   # puis changer MEILI_MASTER_KEY

docker compose --env-file .env up -d
```

Ça suffit : `docker compose up` démarre Meilisearch, télécharge et indexe les
54k adresses SITG (job `ingest`, ~15s, ignoré automatiquement s'il tourne à
nouveau et que l'index est déjà à jour), puis démarre l'API sur
`http://localhost:8000`.

```bash
curl "http://localhost:8000/search?q=Av.%20J-D%20Maillard,%207&limit=3"
curl "http://localhost:8000/health"
```

Pour forcer une réindexation (ex. après une mise à jour des données SITG) :

```bash
docker compose run --rm ingest python -m geocoder_service.ingest --force
```

## Développement local (sans Docker pour l'API)

```bash
docker compose --env-file .env up -d meilisearch   # Meilisearch seul
uv sync

uv run python -m geocoder_service.download   # télécharge + extrait CAD_ADRESSE (idempotent)
uv run python -m geocoder_service.ingest      # (re)crée l'index et charge les 54k adresses
uv run python -m uvicorn geocoder_service.api:app --reload   # API en rechargement à chaud
```

## Utilisation

Directement en Python :

```python
from geocoder_service.search import geocode

geocode("Av. de Thonex 30", limit=3)
# -> liste de hits triés par score décroissant (0-100), ex. :
# [{"adresse": "Avenue de Thônex 30", "commune": "Chêne-Bourg", "score": 100.0, ...}, ...]
```

Ou via l'API HTTP (`GET /search?q=...&limit=...`, `GET /health`) une fois le
service démarré.

Voir aussi [`examples.ipynb`](examples.ipynb) pour des exemples exécutables
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
`compare.py`).

## Limites connues

- Les adresses avec plusieurs fautes de frappe cumulées sur un mot rare et peu
  discriminant (ex. "Jaous" pour "James") peuvent ne retourner aucun résultat
  ("Bvd Jaous Fazy 23") — testé aussi avec un algorithme phonétique dédié au
  français (FONEM, bibliothèque `abydos`), qui n'apporte qu'un gain marginal
  sur nos cas réels (ex. Bessonnette/Bassonette : 85.7 -> 88.9 seulement) et
  n'a donc pas été intégré.
- Scope volontairement limité au canton de Genève (pas de RegBL/BAN).

## Fichiers

- `docker-compose.yml` — Meilisearch, job d'ingestion (unique, idempotent) et API, tout lié
  par un volume `geocoder_data` partagé (l'API a besoin de `data/abbr_to_full.json`,
  généré par l'ingestion).
- `Dockerfile` — image commune à l'API et au job d'ingestion.
- `src/geocoder_service/download.py` — téléchargement/extraction des données (idempotent).
- `src/geocoder_service/ingest.py` — indexation Meilisearch + génération des synonymes
  (idempotent : ignore si l'index est déjà à jour, `--force` pour forcer).
- `src/geocoder_service/score.py` — calcul du score de confiance.
- `src/geocoder_service/search.py` — point d'entrée `geocode()`.
- `src/geocoder_service/api.py` — API HTTP (FastAPI) exposant `geocode()`.
- `src/geocoder_service/compare.py` — script de comparaison sur `tests/test_adresses.csv`.
