FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# Couche dépendances séparée du code pour profiter du cache Docker.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY src ./src
RUN uv sync --frozen

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Au démarrage : attend Meilisearch, télécharge/indexe les données si besoin
# (idempotent), puis lance l'API. Plus besoin d'un service "ingest" séparé.
ENTRYPOINT ["./entrypoint.sh"]
