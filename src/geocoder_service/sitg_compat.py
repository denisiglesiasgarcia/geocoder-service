"""Adapte les résultats de `search.py` au format de l'API SITG Lab v2
(`https://geocodage.sitg-lab.ch/api/v2/search`), pour que des clients écrits
contre cette API (ex. `sitg-geocode`) puissent pointer sur ce service à la
place sans aucun changement de code — juste changer l'URL de base.

geocoder-service étant scopé au canton de Genève (voir README), tous les
hits ont nécessairement `administrativeDivision: "Canton de Genève"` et
`country: "Suisse"` : contrairement à l'API SITG Lab, il n'y a jamais de
résultat hors canton à filtrer.

`EGRID` n'existe pas dans les données source (CAD_ADRESSE, pas de RF) et
vaut donc toujours `None`, comme le permet le schéma `sitg-geocode` (tous
les champs sont nullables).
"""

DATA_SOURCE = {
    "provider": "geocoder-service (SITG CAD_ADRESSE, données ouvertes)",
    "metadataUrl": "https://sitg.ge.ch/donnees/cad-adresse",
    "dataUrl": "https://ge.ch/sitg/geodata/SITG/OPENDATA/CAD_ADRESSE-SHP.zip",
}


def _street_name(adresse: str, house_number: str) -> str:
    """`adresse` est toujours "<nom de rue> <numéro>" (voir ingest.py) : on
    retire le numéro pour obtenir le nom de rue seul, comme `streetName`
    dans la réponse SITG Lab (séparé de `houseNumber`)."""
    suffix = f" {house_number}"
    if house_number and adresse.endswith(suffix):
        return adresse[: -len(suffix)]
    return adresse


def hit_to_sitg_v2(hit: dict) -> dict:
    house_number = str(hit.get("houseNumber") or "")
    postal_code = hit.get("postalCode")
    egid = hit.get("egid")
    return {
        "addressId": hit.get("id"),
        "streetName": _street_name(hit.get("adresse", ""), house_number),
        "houseNumber": house_number or None,
        "postalCode": str(postal_code) if postal_code is not None else None,
        "locality": hit.get("locality"),
        "municipality": hit.get("commune"),
        "type": hit.get("type"),
        "EGID": str(egid) if egid is not None else None,
        "EGRID": None,
        "longitude": hit.get("longitude"),
        "latitude": hit.get("latitude"),
        "coordinates": {"x": hit.get("x"), "y": hit.get("y"), "crs": "EPSG:2056"},
        "administrativeDivision": "Canton de Genève",
        "country": "Suisse",
        "score": hit.get("score"),
        "dataSource": DATA_SOURCE,
        "formatted": None,
    }


def hits_to_sitg_v2_response(
    query: str,
    hits: list[dict],
    *,
    limit: int,
    offset: int,
    n_total_hits: int,
    processing_time_ms: float,
) -> dict:
    """Enveloppe de réponse au format SITG Lab v2 (`{"hits": [...], "query": ..., ...}`).

    `sitg-geocode` (le client de référence) ne lit que la clé `hits` de cette
    enveloppe : le reste n'est là que pour la fidélité au format, au cas où
    d'autres clients l'utiliseraient.
    """
    return {
        "hits": [hit_to_sitg_v2(hit) for hit in hits],
        "query": query,
        "processingTimeMs": round(processing_time_ms, 2),
        "limit": limit,
        "offset": offset,
        "nbHits": n_total_hits,
    }
