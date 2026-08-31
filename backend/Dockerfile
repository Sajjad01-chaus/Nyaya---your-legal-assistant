# syntax=docker/dockerfile:1.7
# Multi-stage build. The API and the worker share this image and differ only by
# entrypoint, so there is one thing to build, scan and push rather than two.

# ----------------------------------------------------------------- builder
FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build-only toolchain; none of it reaches the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

# Wheels are built once here and copied across, so the runtime stage needs no
# compiler. onnxruntime keeps this honest: no torch, so no CUDA wheels.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install -r requirements.txt

# ----------------------------------------------------------------- runtime
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Model weights land on a named volume, not in the image, so the image
    # stays small and a rebuild does not re-download them.
    HF_HOME=/home/nyaya/.cache/huggingface \
    FASTEMBED_CACHE_PATH=/home/nyaya/.cache/fastembed

# Runtime OS packages only:
#   tesseract  - OCR fallback for scanned or unusable text layers
#   libgl/glib - OpenCV headless still links against these for deskewing
#   curl       - HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root from here on.
RUN groupadd --gid 1001 nyaya \
    && useradd --uid 1001 --gid 1001 --create-home --shell /usr/sbin/nologin nyaya

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=nyaya:nyaya . .

RUN mkdir -p /data/uploads /home/nyaya/.cache \
    && chown -R nyaya:nyaya /data /home/nyaya/.cache

USER nyaya

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
