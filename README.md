# geocoder-service

Géocodeur d'adresses du canton de Genève, basé sur les données ouvertes SITG
(`CAD_ADRESSE`) et Meilisearch. Alternative auto-hébergée à l'API SITG Lab
(`geocodage.sitg-lab.ch`), scopée volontairement au canton de Genève.

## Démarrage à partir d'un checkout propre

```bash
cd geocoder-service
cp .env.example .env   # puis changer MEILI_MASTER_KEY

docker compose --env-file .env up -d   # démarre Meilisearch
uv sync

uv run python -m geocoder_service.download   # télécharge + extrait CAD_ADRESSE (idempotent)
uv run python -m geocoder_service.ingest      # (re)crée l'index et charge les 54k adresses
```

## Utilisation

```python
from geocoder_service.search import geocode

geocode("Av. de Thonex 30", limit=3)
# -> liste de hits triés par score décroissant (0-100), ex. :
# [{"adresse": "Avenue de Thônex 30", "commune": "Chêne-Bourg", "score": 100.0, ...}, ...]
```

## Comment ça marche

- **Données** : `CAD_ADRESSE-SHP.zip` (open data SITG), ré-extraites à la demande.
  Le fichier `.cpg` indique un encodage UTF-8 — c'est déjà géré dans `ingest.py`.
- **Synonymes/abréviations** : construits automatiquement à partir des couples
  `TYPABR`/`TYVOIE` réellement présents dans les données (`ch.`↔`Chemin`,
  `av.`↔`Avenue`, etc.), persistés dans `data/abbr_to_full.json` et réutilisés
  à la fois pour les synonymes Meilisearch et pour le score.
- **Score (0-100)** : calculé côté client (`score.py`), pas seulement le
  `_rankingScore` interne de Meilisearch (trop grossier). Deux signaux :
  similarité textuelle (`rapidfuzz.token_set_ratio`, sur adresse et
  adresse+localité, en gardant le maximum) + bonus/malus sur la concordance
  exacte du numéro de rue (une plage en fin de requête comme "51-53" accepte
  l'une ou l'autre borne, les deux existant souvent comme adresses distinctes).
- **`ingest.py` vérifie que le nombre de documents indexés correspond au nombre
  d'enregistrements source** et fait échouer l'ingestion sinon. Une ingestion
  précédente avait silencieusement perdu ~18% des adresses (deux lots de 5000
  rejetés par Meilisearch à cause d'IDPADR avec un espace parasite) sans qu'aucune
  erreur ne remonte — d'où ce garde-fou explicite plutôt qu'un simple print.

Vérifié sur les 107 adresses de `notebooks/test_adresses.csv` : 92/107 avec un
score ≥ 95, contre 61/107 pour l'API SITG Lab en v2 au même seuil (voir
`compare.py`).

## Limites connues

- Les abréviations de prénoms composés (ex. "J-D" pour "Jacob-Daniel") ne sont
  pas résolues — nécessiterait un dictionnaire dédié.
- Les adresses avec plusieurs fautes de frappe cumulées (ex. abréviation de
  type de voie non reconnue + nom de rue mal orthographié) peuvent ne
  retourner aucun résultat (ex. "Bvd Jaous Fazy 23").
- Scope volontairement limité au canton de Genève (pas de RegBL/BAN).

## Fichiers

- `docker-compose.yml` — Meilisearch (image pinnée, volume persistant).
- `src/geocoder_service/download.py` — téléchargement/extraction des données (idempotent).
- `src/geocoder_service/ingest.py` — indexation Meilisearch + génération des synonymes.
- `src/geocoder_service/score.py` — calcul du score de confiance.
- `src/geocoder_service/search.py` — point d'entrée `geocode()`.
- `src/geocoder_service/compare.py` — script de comparaison sur `notebooks/test_adresses.csv`.
