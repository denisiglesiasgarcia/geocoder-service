FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# Couche dépendances séparée du code pour profiter du cache Docker.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY src ./src
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "geocoder_service.api:app", "--host", "0.0.0.0", "--port", "8000"]
