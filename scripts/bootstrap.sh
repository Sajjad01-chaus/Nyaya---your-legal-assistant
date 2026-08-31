#!/usr/bin/env bash
# One-shot bootstrap for the compose stack: ensure schema, index the bare act,
# extract the Second Schedule forms.
#
# Idempotent by design (scripts/ingest.py re-upserts rather than duplicating),
# so `docker compose --profile bootstrap up` is safe to run repeatedly.
#
# PYTHONPATH note: the image is built from ./backend and copies it to /app, so
# the package lives at /app/app. This script is bind-mounted to /app/scripts,
# which puts /app/scripts (not /app) on sys.path by default - hence the export.

set -euo pipefail

export PYTHONPATH="${PYTHONPATH:-/app}"

SOURCE_PDF="${NYAYA_SOURCE_PDF:-/data/raw/bnss_2023.pdf}"
FORMS_DIR="${NYAYA_FORMS_DIR:-/data/forms}"
QDRANT_URL="${NYAYA_QDRANT_URL:-http://qdrant:6333}"

echo "--> waiting for Qdrant at ${QDRANT_URL}"
for i in $(seq 1 60); do
    if curl -fsS "${QDRANT_URL}/readyz" >/dev/null 2>&1; then
        echo "    Qdrant ready"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "ERROR: Qdrant did not become ready within 120s" >&2
        exit 1
    fi
    sleep 2
done

if [ ! -f "${SOURCE_PDF}" ]; then
    echo "ERROR: source PDF not found at ${SOURCE_PDF}" >&2
    echo "The compose file mounts ./data to /data; put the bare act at" >&2
    echo "  data/raw/$(basename "${SOURCE_PDF}")" >&2
    exit 1
fi

mkdir -p "${FORMS_DIR}"

echo "--> indexing statute and extracting forms"
exec python /app/scripts/ingest.py \
    --pdf "${SOURCE_PDF}" \
    --forms-dir "${FORMS_DIR}" \
    "$@"
