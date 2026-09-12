# ChronoSolve ships as one container: the API serves the built UI from the same
# origin, so a free hosting tier only has to run a single web service.

# --- build the frontend -------------------------------------------------
FROM node:24-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- runtime ------------------------------------------------------------
FROM python:3.13-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install dependencies first so edits to the source do not rebuild this layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The app migrates and seeds itself on first start, so it needs the Alembic
# config and the rehearsed fixture it seeds from.
COPY alembic.ini ./
COPY backend/ ./backend/
COPY data/fixtures/ ./data/fixtures/
COPY data/published_baseline.json ./data/published_baseline.json
COPY --from=ui /ui/dist ./frontend/dist

# Small free tiers offer roughly one core; give the solver a budget that fits
# rather than one tuned for a 16-core laptop.
ENV CHRONOSOLVE_WORKERS=2 \
    CHRONOSOLVE_GENERATE_SECONDS=20 \
    CHRONOSOLVE_REPAIR_SECONDS=15 \
    PORT=8000

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app.api:app --host 0.0.0.0 --port ${PORT}"]
