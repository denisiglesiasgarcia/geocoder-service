"""API HTTP exposant le géocodeur (voir search.py pour la logique de recherche)."""

from fastapi import FastAPI, HTTPException, Query

from geocoder_service.search import geocode, get_client

app = FastAPI(
    title="geocoder-service",
    description="Géocodeur d'adresses du canton de Genève (données ouvertes SITG + Meilisearch).",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    """Vérifie que le service et Meilisearch répondent (utilisé par le healthcheck Docker)."""
    try:
        status = get_client().health()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Meilisearch indisponible : {e}") from e
    return {"status": "ok", "meilisearch": status}


@app.get("/search")
def search(
    q: str = Query(..., min_length=1, description="Adresse à géocoder"),
    limit: int = Query(5, ge=1, le=50, description="Nombre maximum de résultats"),
) -> dict:
    """Géocode une adresse, résultats triés par score de confiance (0-100) décroissant."""
    hits = geocode(q, limit=limit)
    return {"query": q, "hits": hits}
