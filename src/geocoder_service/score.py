"""
Système de score de géocodage (0-100), transparent et explicable.

Meilisearch fournit un `_rankingScore` interne (0-1) basé sur ses règles de
classement, mais celui-ci est calculé par paliers grossiers (nombre de règles
satisfaites) et ne distingue pas finement deux résultats proches. On calcule
donc notre propre score, à partir de deux signaux indépendants :

1. Similarité textuelle (rapidfuzz, token_set_ratio — insensible aux tokens
   supplémentaires non partagés, comme la commune si la requête ne la mentionne
   pas) entre la requête et l'adresse candidate, toutes deux normalisées
   (minuscules, sans accents, abréviations développées via la table extraite
   des données SITG).
2. Concordance du numéro de rue : bonus s'il correspond exactement, pénalité
   notable s'il est présent dans la requête mais différent du candidat (une
   rue correcte avec le mauvais numéro reste un mauvais géocodage). Une plage
   en fin de requête (ex. "51-53") accepte l'une ou l'autre des deux bornes.
"""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz

DATA_DIR = Path(__file__).parent.parent.parent / "data"
ABBR_TO_FULL_PATH = DATA_DIR / "abbr_to_full.json"

_HOUSE_NUMBER_BONUS = 5.0
_HOUSE_NUMBER_MISMATCH_PENALTY = 25.0


@lru_cache(maxsize=1)
def _abbr_to_full() -> dict[str, str]:
    if not ABBR_TO_FULL_PATH.exists():
        return {}
    return json.loads(ABBR_TO_FULL_PATH.read_text(encoding="utf-8"))


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_address(text: str) -> str:
    """Normalise une adresse pour comparaison : minuscules, sans accents,
    ponctuation retirée, abréviations de type de voie développées."""
    abbr_to_full = _abbr_to_full()
    text = _strip_accents(text.lower())
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = text.split()
    expanded = [_strip_accents(abbr_to_full.get(tok, tok).lower()) for tok in tokens]
    return " ".join(expanded)


_HOUSE_NUMBER = r"\d+[a-zA-Z]?"
_RANGE_AT_END_RE = re.compile(rf"({_HOUSE_NUMBER})\s*-\s*({_HOUSE_NUMBER})\s*$")


def extract_house_numbers(text: str) -> set[str]:
    """Retourne le ou les numéros de rue acceptables pour cette requête.

    Le numéro de rue est toujours en fin d'adresse en Suisse/France ; certains
    noms de rue contiennent eux-mêmes un nombre (ex. "Rue du 31-Décembre 68"),
    qu'il ne faut pas confondre avec le numéro réel — d'où le dernier nombre,
    et non le premier.

    Une plage en fin de requête (ex. "51-53") accepte les deux bornes : les
    deux existent souvent comme adresses distinctes dans les données (vérifié
    sur "Chemin de la Montagne 51" et "53", deux EGID différents).
    """
    range_match = _RANGE_AT_END_RE.search(text)
    if range_match:
        return {range_match.group(1).lower(), range_match.group(2).lower()}

    matches = re.findall(rf"\b{_HOUSE_NUMBER}\b", text)
    return {matches[-1].lower()} if matches else set()


def compute_score(query: str, hit: dict) -> float:
    """Calcule un score de confiance 0-100 pour un hit Meilisearch donné une requête.

    La similarité est calculée à la fois sur l'adresse seule et sur
    adresse + localité/commune, et on garde le maximum : mentionner
    correctement "Genève" doit pouvoir aider le score, mais son absence
    ne doit jamais en diluer un qui serait déjà parfait sans elle.
    """
    normalized_query = normalize_address(query)
    address_only = normalize_address(hit.get("adresse", ""))
    with_locality = normalize_address(
        " ".join(p for p in [hit.get("adresse", ""), hit.get("locality", ""), hit.get("commune", "")] if p)
    )
    similarity = max(
        fuzz.token_set_ratio(normalized_query, address_only),
        fuzz.token_set_ratio(normalized_query, with_locality),
    )

    query_numbers = extract_house_numbers(query)
    hit_number = str(hit.get("houseNumber") or "").lower()

    score = similarity
    if query_numbers and hit_number:
        if hit_number in query_numbers:
            score = min(100.0, score + _HOUSE_NUMBER_BONUS)
        else:
            score = max(0.0, score - _HOUSE_NUMBER_MISMATCH_PENALTY)

    return round(score, 2)
