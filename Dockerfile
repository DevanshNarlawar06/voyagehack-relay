# Relay API. Migrates, seeds demo users, then serves. The worker (WP4) can reuse this image.
FROM python:3.12-slim
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY relay ./relay
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY config ./config
COPY definitions ./definitions
COPY seed ./seed
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync relay db upgrade && uv run --no-sync relay seed users && uv run --no-sync uvicorn relay.bootstrap:create_app --factory --host 0.0.0.0 --port 8000"]
