"""Télécharge (si nécessaire) et extrait les données ouvertes SITG CAD_ADRESSE."""

import zipfile
from pathlib import Path
from urllib.request import urlretrieve

DATA_URL = "https://ge.ch/sitg/geodata/SITG/OPENDATA/CAD_ADRESSE-SHP.zip"
DATA_DIR = Path(__file__).parent.parent.parent / "data"
ZIP_PATH = DATA_DIR / "CAD_ADRESSE-SHP.zip"
SHP_PATH = DATA_DIR / "CAD_ADRESSE.shp"


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if SHP_PATH.exists():
        print(f"{SHP_PATH.name} déjà présent, téléchargement ignoré.")
        return

    if not ZIP_PATH.exists():
        print(f"Téléchargement de {DATA_URL} ...")
        # DATA_URL est une constante HTTPS fixe du module, jamais une entrée
        # utilisateur : le risque visé par B310 (schéma d'URL arbitraire, ex.
        # file://) ne s'applique pas ici.
        urlretrieve(DATA_URL, ZIP_PATH)  # nosec B310

    print(f"Extraction de {ZIP_PATH.name} ...")
    with zipfile.ZipFile(ZIP_PATH) as zf:
        zf.extractall(DATA_DIR)

    print("Données prêtes.")


if __name__ == "__main__":
    main()
