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
   des données SITG). Certaines abréviations sont ambiguës (ex. "j-d" ->
   "Jean-Daniel" OU "Jacob-Daniel") : on essaie toutes les interprétations de
   la requête et on garde la meilleure — le reste de l'adresse (nom de rue,
   numéro) départage naturellement.
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
def _abbr_to_full() -> dict[str, list[str]]:
    if not ABBR_TO_FULL_PATH.exists():
        return {}
    return json.loads(ABBR_TO_FULL_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _abbr_regex() -> re.Pattern | None:
    """Regex unique pour toutes les abréviations, y compris celles à plusieurs
    caractères non-mots (ex. "j-d", "rd-pt."). Doit s'appliquer AVANT le retrait
    de la ponctuation, sinon une clé comme "j-d" serait déjà scindée en deux
    tokens isolés ("j", "d") au moment de la recherche dans le dictionnaire.
    """
    abbr_to_full = _abbr_to_full()
    if not abbr_to_full:
        return None
    keys = sorted(abbr_to_full.keys(), key=len, reverse=True)
    pattern = "|".join(re.escape(k) for k in keys)
    return re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.IGNORECASE)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _finalize(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def normalize_address(text: str) -> str:
    """Normalise un texte de référence (ex. l'adresse officielle d'un hit) :
    minuscules, sans accents, abréviations développées (première forme connue
    si plusieurs existent), ponctuation retirée. Les adresses officielles ne
    contiennent normalement pas d'abréviation ambiguë : pour la requête
    utilisateur, préférer `normalize_address_variants`.
    """
    abbr_to_full = _abbr_to_full()
    stripped = _strip_accents(text.lower())
    regex = _abbr_regex()
    if regex is not None:
        stripped = regex.sub(
            lambda m: _strip_accents(abbr_to_full.get(m.group(0).lower(), [m.group(0)])[0].lower()),
            stripped,
        )
    return _finalize(stripped)


def normalize_address_variants(text: str) -> list[str]:
    """Comme `normalize_address`, mais retourne une variante par interprétation
    possible si le texte contient une abréviation ambiguë (ex. "j-d" peut
    désigner "Jean-Daniel" ou "Jacob-Daniel"). Le reste des abréviations n'est
    développé qu'une fois (première forme connue) pour éviter une explosion
    combinatoire ; en pratique une requête ne contient jamais deux abréviations
    ambiguës en même temps.
    """
    abbr_to_full = _abbr_to_full()
    regex = _abbr_regex()
    stripped = _strip_accents(text.lower())
    if regex is None:
        return [_finalize(stripped)]

    ambiguous_match = next(
        (m for m in regex.finditer(stripped) if len(abbr_to_full.get(m.group(0).lower(), [])) > 1),
        None,
    )
    if ambiguous_match is None:
        return [normalize_address(text)]

    ambiguous_span = ambiguous_match.span()
    candidates = abbr_to_full[ambiguous_match.group(0).lower()]

    variants = []
    for candidate in candidates:
        chosen = _strip_accents(candidate.lower())

        def repl(m: re.Match, _chosen: str = chosen, _span: tuple[int, int] = ambiguous_span) -> str:
            if m.span() == _span:
                return _chosen
            return _strip_accents(abbr_to_full.get(m.group(0).lower(), [m.group(0)])[0].lower())

        variants.append(_finalize(regex.sub(repl, stripped)))
    return variants


_HOUSE_NUMBER = r"\d+[a-zA-Z]*"
_RANGE_AT_END_RE = re.compile(rf"({_HOUSE_NUMBER})\s*-\s*({_HOUSE_NUMBER})\s*$")

# Suffixes de numéro de rue : abréviation d'une lettre -> forme complète.
# Vérifié sur les données (740 adresses en "BIS", 82 en "TER" dans CAD_ADRESSE) :
# les utilisateurs écrivent couramment "7b"/"7t" pour "7bis"/"7ter".
_HOUSE_NUMBER_SUFFIX_EXPANSION = {"b": "bis", "t": "ter"}
_HOUSE_NUMBER_SUFFIX_RE = re.compile(r"^(\d+)([a-z]+)$")


def _normalize_house_number(number: str) -> str:
    match = _HOUSE_NUMBER_SUFFIX_RE.match(number.lower())
    if not match:
        return number.lower()
    digits, suffix = match.groups()
    return digits + _HOUSE_NUMBER_SUFFIX_EXPANSION.get(suffix, suffix)


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
        return {
            _normalize_house_number(range_match.group(1)),
            _normalize_house_number(range_match.group(2)),
        }

    matches = re.findall(rf"\b{_HOUSE_NUMBER}\b", text)
    return {_normalize_house_number(matches[-1])} if matches else set()


def compute_score(query: str, hit: dict) -> float:
    """Calcule un score de confiance 0-100 pour un hit Meilisearch donné une requête.

    La similarité est calculée sur toutes les variantes normalisées de la
    requête (une seule si aucune abréviation ambiguë), à la fois contre
    l'adresse seule et adresse+localité, en gardant le maximum : mentionner
    correctement "Genève" doit pouvoir aider le score, mais son absence
    ne doit jamais en diluer un qui serait déjà parfait sans elle.
    """
    query_variants = normalize_address_variants(query)
    address_only = normalize_address(hit.get("adresse", ""))
    with_locality = normalize_address(
        " ".join(p for p in [hit.get("adresse", ""), hit.get("locality", ""), hit.get("commune", "")] if p)
    )

    similarity = max(
        fuzz.token_set_ratio(q, target)
        for q in query_variants
        for target in (address_only, with_locality)
    )

    query_numbers = extract_house_numbers(query)
    hit_number = _normalize_house_number(str(hit.get("houseNumber") or ""))

    score = similarity
    if query_numbers and hit_number:
        if hit_number in query_numbers:
            score = min(100.0, score + _HOUSE_NUMBER_BONUS)
        else:
            score = max(0.0, score - _HOUSE_NUMBER_MISMATCH_PENALTY)

    return round(score, 2)
