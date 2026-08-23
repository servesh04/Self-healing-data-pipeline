# Python 3.14 to match local dev exactly — pandas 3 / pandera 0.32 have cp314
# wheels, so there is no reason to split the local and deployed runtimes.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so edits to source do not invalidate the wheel layer.
# psycopg[binary] ships a bundled libpq, so no build toolchain is needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects $PORT and it is not knowable at build time; shell form so it
# expands at runtime. 8000 is the local fallback.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
