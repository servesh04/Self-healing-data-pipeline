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
# Dependencies first so edits to source do not invalidate the wheel layer.
# psycopg[binary] ships a bundled libpq, so no build toolchain is needed.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Application code lands at /app so `uvicorn main:app` resolves.
COPY backend/ ./

# Render injects $PORT and it is not knowable at build time; shell form so it
# expands at runtime. 8000 is the local fallback.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
