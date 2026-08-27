# EAGLE-X — single-service production image.
# Stage 1: build the exported Next.js frontend (frontend/out).
# Stage 2: install backend deps (requirements.txt, pinned) into a Python runtime.
# Runtime: uvicorn serves the API, and FRONTEND_DIR serves the static export from the
# same origin. Port from $PORT (Render) or 8000.

# ---------- Stage 1: frontend build ----------
FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: backend deps ----------
FROM python:3.12-slim AS backend
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY backend/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ---------- Runtime ----------
FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

RUN groupadd --system eaglex && useradd --system --gid eaglex --no-create-home eaglex

COPY --from=backend /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=backend /usr/local/bin /usr/local/bin
COPY backend/ ./backend/

# Serve the pre-built frontend from the exported static dir.
COPY --from=frontend /build/frontend/out /app/frontend_static

# Writable runtime data + config dirs for the JSON-based persistence store.
RUN mkdir -p /app/data /app/backend/.data && chown -R eaglex:eaglex /app

ENV FRONTEND_DIR=/app/frontend_static
USER eaglex

EXPOSE 8000
CMD ["sh", "-c", "cd backend && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]