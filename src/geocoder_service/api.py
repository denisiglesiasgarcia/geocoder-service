"""API HTTP exposant le géocodeur (voir search.py pour la logique de recherche)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query

from geocoder_service.search import aclose_async_client, geocode_async, get_async_client


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
