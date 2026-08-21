# 🦅 EAGLE-X

Trading intelligence platform for Deriv synthetic indices (R_10, R_25, R_50, R_75, R_100).

## Architecture

```
frontend/  Next.js 14 (dashboard, splash, learn, videos)
backend/   FastAPI + async tick ingestion, analytics, auto-trader
```

## Quick start

```bash
make install
make dev-backend   # http://localhost:8000
make dev-frontend  # http://localhost:3000
```

## Tests

```bash
make test          # pytest backend (~50 tests)
```

## Deploy

- Docker images provided for backend/frontend (`Dockerfile` in each).
- Render blueprint in `render.yaml`.
- See full 100-page system documentation for details.

## Disclaimer

Statistical analysis tool — NOT a guaranteed-profit engine. Past performance does not guarantee future results.
