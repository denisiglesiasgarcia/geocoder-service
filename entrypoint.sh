#!/bin/sh
set -e

# Attend que Meilisearch réponde (utile même hors docker-compose, où
# depends_on/healthcheck ne s'applique pas).
python -c "
import os
import time
import meilisearch

url = os.environ.get('MEILI_URL', 'http://meilisearch:7700')
key = os.environ.get('MEILI_MASTER_KEY')
client = meilisearch.Client(url, key)
for _ in range(60):
    try:
        client.health()
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit(f'Meilisearch injoignable sur {url} après 60s')
"

# Télécharge les données si besoin (idempotent) et (re)construit l'index si
# besoin (ingest.py compare le nombre de documents et ne fait rien si l'index
# est déjà à jour — donc un redémarrage de conteneur ne relance pas tout).
python -m geocoder_service.download
python -m geocoder_service.ingest

exec uvicorn geocoder_service.api:app --host 0.0.0.0 --port 8000
