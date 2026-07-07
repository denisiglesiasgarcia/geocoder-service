"""Recherche d'adresses : récupère des candidats depuis Meilisearch, puis les
reclasse avec notre propre score de confiance (voir score.py)."""

import os

import meilisearch

from geocoder_service.score import compute_score

MEILI_URL = os.environ.get("MEILI_URL", "http://localhost:7700")
MEILI_MASTER_KEY = os.environ.get("MEILI_MASTER_KEY", "dev_master_key_change_me")
INDEX_NAME = "adresses_ge"

_client: meilisearch.Client | None = None


def _get_index():
    global _client
    if _client is None:
        _client = meilisearch.Client(MEILI_URL, MEILI_MASTER_KEY)
    return _client.index(INDEX_NAME)


def geocode(query: str, *, limit: int = 5, candidate_pool: int = 20) -> list[dict]:
    """Géocode une adresse et retourne jusqu'à `limit` résultats, triés par score décroissant.

    candidate_pool : nombre de candidats demandés à Meilisearch avant reclassement
    (plus grand que `limit` pour laisser une chance à un résultat moins bien classé
    par Meilisearch mais mieux noté par notre score, ex. bon numéro de rue).
    """
    index = _get_index()
    result = index.search(query, {"limit": candidate_pool})

    scored = [{**hit, "score": compute_score(query, hit)} for hit in result["hits"]]
    scored.sort(key=lambda h: h["score"], reverse=True)

    return scored[:limit]
