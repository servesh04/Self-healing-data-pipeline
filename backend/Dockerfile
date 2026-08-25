# Python 3.14 to match local dev exactly — pandas 3 / pandera 0.32 have cp314
# wheels, so there is no reason to split the local and deployed runtimes.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# NOTE: the build context is the REPOSITORY ROOT, not backend/. That is
# Render's dashboard default, and from Phase 1 the backend also needs config/
# and data/ which live outside backend/. All COPY paths are repo-relative.
#
# The image mirrors the repo's own layout under /app (backend/, config/,
# data/ as siblings) instead of flattening backend/'s contents into /app --
# several modules locate config/data by walking a fixed number of parent
# directories from their own file (backend/pipeline/contract.py's
# CONTRACT_PATH, backend/routers/runs.py's DATA_SOURCES_DIR, both
# `Path(__file__).resolve().parents[2]`), the same arithmetic that finds the
# repo root two levels above backend/pipeline/ or backend/routers/ locally.
# Flattening backend/ into /app (an earlier version of this file did) shifts
# that arithmetic by one directory level *and* never copied config/ or
# data/ into the image at all -- every real fault-dataset run on the
# deployed backend failed with FileNotFoundError, silently, because nothing
# had ever actually exercised POST /api/runs against the deployed image
# before. Found 2026-08-25 via a real stuck run, not a code read.
#
# Dependencies first so edits to source do not invalidate the wheel layer.
# psycopg[binary] ships a bundled libpq, so no build toolchain is needed.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY config/ ./config/
COPY data/ ./data/

# Run from /app/backend so `uvicorn main:app` resolves exactly as it does
# locally (`cd backend && python run_local.py`) -- same working directory,
# same relative-import assumptions, same parents[2] arithmetic.
#
# Render injects $PORT and it is not knowable at build time; shell form so it
# expands at runtime. 8000 is the local fallback.
EXPOSE 8000
CMD ["sh", "-c", "cd backend && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
