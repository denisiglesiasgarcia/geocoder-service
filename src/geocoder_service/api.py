"""API HTTP exposant le géocodeur (voir search.py pour la logique de recherche)."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query

from geocoder_service.search import aclose_async_client, geocode_async, geocode_async_raw, get_async_client
from geocoder_service.sitg_compat import hits_to_sitg_v2_response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await aclose_async_client()


app = FastAPI(
    title="geocoder-service",
    description="Géocodeur d'adresses du canton de Genève (données ouvertes SITG + Meilisearch).",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    """Vérifie que le service et Meilisearch répondent (utilisé par le healthcheck Docker)."""
    try:
        response = await get_async_client().get("/health")
        response.raise_for_status()
        status = response.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"Meilisearch indisponible : {e}") from e
    return {"status": "ok", "meilisearch": status}


@app.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Adresse à géocoder"),
    limit: int = Query(5, ge=1, le=50, description="Nombre maximum de résultats"),
) -> dict:
    """Géocode une adresse, résultats triés par score de confiance (0-100) décroissant."""
    hits = await geocode_async(q, limit=limit)
    return {"query": q, "hits": hits}


@app.get("/api/v2/search")
async def search_sitg_v2(
    q: str = Query(..., min_length=1, description="Adresse à géocoder"),
    limit: int = Query(5, ge=1, le=50, description="Nombre maximum de résultats"),
    offset: int = Query(0, ge=0, description="Décalage pour la pagination"),
    suggest: bool = Query(False, description="Accepté pour compatibilité, sans effet ici"),
) -> dict:
    """Même géocodeur que `/search`, mais réponse au format de l'API SITG Lab v2
    (`geocodage.sitg-lab.ch/api/v2/search`) : un client écrit contre cette API
    (ex. `sitg-geocode`) peut pointer sur ce service à la place en changeant
    uniquement l'URL de base, sans aucun changement de code (voir sitg_compat.py).
    """
    del suggest  # accepté pour compatibilité de signature, non utilisé
    start = time.perf_counter()
    result = await geocode_async_raw(q, limit=limit, offset=offset)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return hits_to_sitg_v2_response(
        q,
        result["hits"],
        limit=limit,
        offset=offset,
        n_total_hits=result["estimatedTotalHits"],
        processing_time_ms=elapsed_ms,
    )
