# EAGLE-X — single image: frontend build + backend runtime.
# The FastAPI backend serves the statically-exported frontend from the same
# origin, so the whole platform is ONE service on ONE port. No proxy.

# ---- Stage 1: build the Pro Trader twin frontend (static export) ----
FROM node:18-alpine AS frontend
WORKDIR /fe
COPY twin/package.json twin/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY twin/ ./
RUN npm run build   # produces /fe/out

# ---- Stage 2: Python deps ----
FROM python:3.11-slim AS deps
WORKDIR /app
RUN pip install poetry==1.7.1
COPY backend/pyproject.toml backend/poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --only main

# ---- Stage 3: runtime ----
FROM python:3.11-slim
WORKDIR /app
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY backend/ ./
COPY --from=frontend /fe/out ./frontend_static

ENV PYTHONUNBUFFERED=1
ENV FRONTEND_DIR=/app/frontend_static
# Render injects PORT; default 8000 for local/docker-compose use.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4)" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
