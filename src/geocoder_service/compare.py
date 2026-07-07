"""Mesure le taux de correspondance à haute confiance (score >= 95) sur un
échantillon d'adresses réelles, pour suivre la qualité au fil des changements."""

import csv
from pathlib import Path

from geocoder_service.search import geocode

TEST_CSV = Path(__file__).parent.parent.parent / "tests" / "test_adresses.csv"
SCORE_THRESHOLD = 95.0


def main() -> None:
    with open(TEST_CSV, encoding="utf-8") as f:
        addresses = [row[0] for row in csv.reader(f)][1:]

    n_ge_threshold = 0
    n_any = 0
    below: list[tuple[float | None, str, str | None]] = []

    for adr in addresses:
        hits = geocode(adr, limit=1)
        if hits:
            n_any += 1
            top = hits[0]
            if top["score"] >= SCORE_THRESHOLD:
                n_ge_threshold += 1
            else:
                below.append((top["score"], adr, top["adresse"]))
        else:
            below.append((None, adr, None))

    print(f"{n_ge_threshold}/{len(addresses)} avec score >= {SCORE_THRESHOLD}")
    print(f"{n_any}/{len(addresses)} avec au moins un résultat")
    if below:
        print("\nSous le seuil (ou sans résultat) :")
        for score, adr, matched in sorted(below, key=lambda x: (x[0] is None, x[0] or 0)):
            score_str = f"{score:.2f}" if score is not None else "NONE"
            print(f"  {score_str:>6}  {adr:45s} -> {matched}")


if __name__ == "__main__":
    main()
