"""Recherche d'adresses : récupère des candidats depuis Meilisearch, puis les
reclasse avec notre propre score de confiance (voir score.py).

Deux clients Meilisearch coexistent : le client synchrone officiel
(`meilisearch`), utilisé par `geocode()` (compare.py, notebooks, scripts) ;
et un client HTTP asynchrone (`httpx`) pour `geocode_async()`/`api.py`, le
paquet `meilisearch` officiel n'ayant pas de client async.

Les deux utilisent une clé API `search`-only (voir `_search_api_key` et
`ingest.py::_write_search_api_key`) plutôt que MEILI_MASTER_KEY : le serveur
qui répond aux requêtes HTTP publiques n'a ainsi jamais besoin d'un accès
admin à Meilisearch (suppression d'index, gestion des clés, etc.)."""

import os
from pathlib import Path

import httpx
import meilisearch

from geocoder_service.score import compute_score

MEILI_URL = os.environ.get("MEILI_URL", "http://localhost:7700")
MEILI_MASTER_KEY = os.environ.get("MEILI_MASTER_KEY", "dev_master_key_change_me")
SEARCH_API_KEY_PATH = (
    Path(__file__).parent.parent.parent / "data" / "search_api_key.txt"
)
INDEX_NAME = "adresses_ge"

_client: meilisearch.Client | None = None
_async_client: httpx.AsyncClient | None = None


def _search_api_key() -> str:
    """Clé `search`-only écrite par `ingest.py` au démarrage. Si absente (ex.
    ingestion pas encore lancée, environnement de dev sans Docker), on
    retombe sur MEILI_MASTER_KEY plutôt que d'échouer."""
    try:
        return SEARCH_API_KEY_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return MEILI_MASTER_KEY


def get_client() -> meilisearch.Client:
    global _client
    if _client is None:
        _client = meilisearch.Client(MEILI_URL, _search_api_key())
    return _client


def _get_index():
    return get_client().index(INDEX_NAME)


def get_async_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(
            base_url=MEILI_URL,
            headers={"Authorization": f"Bearer {_search_api_key()}"},
        )
    return _async_client


async def aclose_async_client() -> None:
    """À appeler à l'arrêt de l'application (voir `lifespan` dans api.py),
    pour libérer proprement les connexions HTTP ouvertes par le pool."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None


def _rank(query: str, hits: list[dict], limit: int, offset: int = 0) -> list[dict]:
    scored = [{**hit, "score": compute_score(query, hit)} for hit in hits]
    scored.sort(key=lambda h: h["score"], reverse=True)
    return scored[offset : offset + limit]


def geocode(
    query: str, *, limit: int = 5, offset: int = 0, candidate_pool: int = 20
) -> list[dict]:
    """Géocode une adresse et retourne jusqu'à `limit` résultats (à partir de `offset`),
    triés par score décroissant.

    candidate_pool : nombre de candidats demandés à Meilisearch avant reclassement
    (plus grand que `offset + limit` pour laisser une chance à un résultat moins bien
    classé par Meilisearch mais mieux noté par notre score, ex. bon numéro de rue).
    """
    candidate_pool = max(candidate_pool, offset + limit)
    index = _get_index()
    result = index.search(query, {"limit": candidate_pool})
    return _rank(query, result["hits"], limit, offset)


async def geocode_async_raw(
    query: str, *, limit: int = 5, offset: int = 0, candidate_pool: int = 20
) -> dict:
    """Comme `geocode_async`, mais retourne aussi `estimatedTotalHits` (compte
    total de candidats Meilisearch, indépendant de `limit`/`offset`) — utilisé
    par la couche de compatibilité SITG Lab (voir sitg_compat.py) pour `nbHits`."""
    candidate_pool = max(candidate_pool, offset + limit)
    client = get_async_client()
    response = await client.post(
        f"/indexes/{INDEX_NAME}/search",
        json={"q": query, "limit": candidate_pool},
    )
    response.raise_for_status()
    result = response.json()
    return {
        "hits": _rank(query, result["hits"], limit, offset),
        "estimatedTotalHits": result.get("estimatedTotalHits", len(result["hits"])),
    }


async def geocode_async(
    query: str, *, limit: int = 5, offset: int = 0, candidate_pool: int = 20
) -> list[dict]:
    """Équivalent asynchrone de `geocode()`, pour l'API HTTP (voir api.py) :
    ne bloque pas la boucle d'événements pendant l'appel réseau à Meilisearch."""
    result = await geocode_async_raw(
        query, limit=limit, offset=offset, candidate_pool=candidate_pool
    )
    return result["hits"]
