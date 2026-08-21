FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY scripts ./scripts
COPY migrations ./migrations
COPY ui ./ui
COPY PRODUCTION-GATE-REGISTRY.yaml ./PRODUCTION-GATE-REGISTRY.yaml

RUN apt-get update \
    && apt-get install --no-install-recommends -y postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.16 \
    && uv sync --locked --no-dev --no-install-project \
    && uv pip install --system --no-cache .

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8090
ENTRYPOINT ["python", "-m", "buyer_ops_contracts.cli"]
