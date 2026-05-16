# clawstreet-worker: long-lived idle container that n8n execs ingestion scripts into.
# Keeps Python deps prebuilt so each `docker exec` is just a script invocation.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps for psycopg2-binary, numpy/scipy/numba wheels, and TLS to Polygon/Alpaca.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        libpq5 \
        postgresql-client \
        tini \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# scripts/, config/, and .env.* are bind-mounted by docker-compose so edits land instantly.
# Keep the container alive so n8n can `docker exec` into it on every workflow tick.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
