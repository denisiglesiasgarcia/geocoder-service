"""
Ingestion des adresses du canton de Genève (SITG CAD_ADRESSE, données ouvertes)
dans un index Meilisearch.

Source : https://ge.ch/sitg/geodata/SITG/OPENDATA/CAD_ADRESSE-SHP.zip
"""

import json
import os
import re
from pathlib import Path

import meilisearch
import shapefile
from pyproj import Transformer

DATA_DIR = Path(__file__).parent.parent.parent / "data"
SHAPEFILE_PATH = DATA_DIR / "CAD_ADRESSE.shp"
ABBR_TO_FULL_PATH = DATA_DIR / "abbr_to_full.json"

MEILI_URL = os.environ.get("MEILI_URL", "http://localhost:7700")
MEILI_MASTER_KEY = os.environ.get("MEILI_MASTER_KEY", "dev_master_key_change_me")
INDEX_NAME = "adresses_ge"

_LV95_TO_WGS84 = Transformer.from_crs("EPSG:2056", "EPSG:4326", always_xy=True)

# Synonymes manuels non couverts par les abréviations du jeu de données
# (ex. TYPABR pour "rue" est déjà "rue", donc "r." n'apparaît jamais comme clé).
_EXTRA_ABBR_TO_FULL: dict[str, list[str]] = {
    "r": ["Rue"],
    "av": ["Avenue"],
    "bd": ["Boulevard"],
}


def _load_records() -> list[dict]:
    sf = shapefile.Reader(str(SHAPEFILE_PATH), encoding="utf-8")
    fields = [f[0] for f in sf.fields[1:]]
    records = []
    for shape_rec in sf.iterShapeRecords():
        rec = dict(zip(fields, shape_rec.record, strict=True))
        x, y = shape_rec.shape.points[0]
        lon, lat = _LV95_TO_WGS84.transform(x, y)
        records.append(
            {
                # Certains IDPADR source ont des espaces parasites (ex. " 18080715011"),
                # que Meilisearch rejette comme identifiant de document invalide.
                "id": (rec["IDPADR"] or "").strip(),
                "adresse": (rec["ADRESSE"] or "").strip(),
                "typeVoie": rec["TYVOIE"],
                "houseNumber": rec["NO_ADRESSE"],
                "postalCode": rec["NO_POSTAL"],
                "locality": rec["NOM_NPA"],
                "commune": rec["COMMUNE"],
                "type": rec["TYPE"],
                "egid": rec["EGID"],
                "longitude": round(lon, 6),
                "latitude": round(lat, 6),
                "x": round(x, 2),
                "y": round(y, 2),
            }
        )
    return records


def _normalize_key(token: str) -> str:
    return token.strip().rstrip(".").lower()


def _build_typevoie_abbr_to_full() -> dict[str, list[str]]:
    """Construit abréviation (normalisée) -> formes complètes possibles, à partir
    des couples TYPABR/TYVOIE réellement présents dans les données (source de
    vérité). Sans ambiguïté connue, mais on garde une liste pour rester
    cohérent avec _build_forename_abbr_to_full (voir plus bas)."""
    sf = shapefile.Reader(str(SHAPEFILE_PATH), encoding="utf-8")
    fields = [f[0] for f in sf.fields[1:]]
    typabr_idx = fields.index("TYPABR")
    tyvoie_idx = fields.index("TYVOIE")

    abbr_to_full: dict[str, list[str]] = {k: list(v) for k, v in _EXTRA_ABBR_TO_FULL.items()}
    for rec in sf.iterRecords():
        abbr, full = rec[typabr_idx], rec[tyvoie_idx]
        if not abbr or not full:
            continue
        key = _normalize_key(abbr)
        if key and key != _normalize_key(full):
            candidates = abbr_to_full.setdefault(key, [])
            if full not in candidates:
                candidates.append(full)
    return abbr_to_full


def _is_already_abbreviated(liant: str) -> bool:
    """Ex. 'J.-F' est déjà une forme abrégée (chaque partie ne fait qu'une lettre)."""
    parts = re.split(r"[.\-]+", liant)
    return all(len(p) <= 1 for p in parts if p)


def _build_forename_abbr_to_full() -> dict[str, list[str]]:
    """Génère des abréviations d'initiales pour les prénoms composés du champ
    LIANT (ex. 'Jacob-Daniel' -> 'j-d', 'jd', 'j.d'), pour matcher des requêtes
    comme "Av. J-D Maillard" qui n'utilisent pas la forme développée officielle.
    Entièrement dérivé des données, sans dictionnaire externe.

    Les mêmes initiales peuvent correspondre à plusieurs prénoms composés
    distincts (ex. "j-d" -> "Jean-Daniel" ET "Jacob-Daniel" existent tous les
    deux) : on garde donc toutes les correspondances plutôt que d'en choisir
    une arbitrairement, et on laisse le score départager via le reste de
    l'adresse (nom de rue, numéro).
    """
    sf = shapefile.Reader(str(SHAPEFILE_PATH), encoding="utf-8")
    fields = [f[0] for f in sf.fields[1:]]
    liant_idx = fields.index("LIANT")

    abbr_to_full: dict[str, list[str]] = {}
    seen: set[str] = set()
    for rec in sf.iterRecords():
        liant = (rec[liant_idx] or "").strip(" .-")
        if not liant or "-" not in liant or liant in seen:
            continue
        seen.add(liant)
        if _is_already_abbreviated(liant):
            continue

        parts = [p for p in liant.split("-") if p]
        if len(parts) < 2:
            continue
        initials = [p[0] for p in parts]
        for variant in {
            "-".join(initials).lower(),
            ".".join(initials).lower(),
            "".join(initials).lower(),
        }:
            candidates = abbr_to_full.setdefault(variant, [])
            if liant not in candidates:
                candidates.append(liant)
    return abbr_to_full


def _build_abbr_to_full() -> dict[str, list[str]]:
    """Table complète abréviation -> formes complètes possibles : types de voie
    (TYPABR/TYVOIE) et initiales de prénoms composés (LIANT), les deux dérivés
    des données SITG."""
    abbr_to_full = _build_typevoie_abbr_to_full()
    for abbr, candidates in _build_forename_abbr_to_full().items():
        existing = abbr_to_full.setdefault(abbr, [])
        for candidate in candidates:
            if candidate not in existing:
                existing.append(candidate)
    return abbr_to_full


def _build_meilisearch_synonyms(abbr_to_full: dict[str, list[str]]) -> dict[str, list[str]]:
    """Synonymes bidirectionnels pour Meilisearch (abréviation <-> formes complètes)."""
    synonyms: dict[str, list[str]] = {}
    for abbr, candidates in abbr_to_full.items():
        group = synonyms.setdefault(abbr, [])
        for full in candidates:
            if full not in group:
                group.append(full)
            full_key = full.lower()
            full_group = synonyms.setdefault(full_key, [])
            if abbr not in full_group:
                full_group.append(abbr)
    return synonyms


def main() -> None:
    client = meilisearch.Client(MEILI_URL, MEILI_MASTER_KEY)

    abbr_to_full = _build_abbr_to_full()
    ABBR_TO_FULL_PATH.write_text(
        json.dumps(abbr_to_full, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Table abréviation -> forme complète écrite dans {ABBR_TO_FULL_PATH.name} "
          f"({len(abbr_to_full)} entrées)")

    client.create_index(INDEX_NAME, {"primaryKey": "id"})
    index = client.index(INDEX_NAME)

    index.update_settings(
        {
            "searchableAttributes": ["adresse", "commune", "locality"],
            "filterableAttributes": ["postalCode", "commune", "typeVoie"],
            "sortableAttributes": ["postalCode"],
            "synonyms": _build_meilisearch_synonyms(abbr_to_full),
            "rankingRules": [
                "words",
                "typo",
                "proximity",
                "attribute",
                "sort",
                "exactness",
            ],
            "typoTolerance": {
                "enabled": True,
                "minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8},
            },
        }
    )

    records = _load_records()
    print(f"{len(records)} adresses chargées depuis {SHAPEFILE_PATH.name}")

    batch_size = 5000
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        task = index.add_documents(batch)
        finished = client.wait_for_task(task.task_uid, timeout_in_ms=60_000)
        if finished.status != "succeeded":
            raise RuntimeError(
                f"Échec d'indexation du batch {i}-{i + len(batch)} : {finished.error}"
            )
        print(f"  indexé {i + len(batch)}/{len(records)}")

    stats = index.get_stats()
    if stats.number_of_documents != len(records):
        raise RuntimeError(
            f"Incohérence après indexation : {stats.number_of_documents} documents "
            f"indexés pour {len(records)} enregistrements source."
        )

    print(f"Indexation terminée : {stats.number_of_documents} documents.")


if __name__ == "__main__":
    main()
