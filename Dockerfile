# syntax=docker/dockerfile:1
# ---------- stage 1: build the dashboard ----------
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- stage 2: python runtime ----------
FROM python:3.11-slim AS runtime

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SCAI_DATA_DIR=/app/backend/data \
    SCAI_HOST=0.0.0.0 \
    SCAI_PORT=8000 \
    SCAI_MODEL_PATH=/app/models/yolo11n.pt

# OpenCV/ffmpeg runtime dependencies (headless OpenCV still needs these libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libsm6 libxext6 libxrender1 ffmpeg curl espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements-vision.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install --index-url ${TORCH_INDEX_URL} torch torchvision \
    && pip install ultralytics

COPY backend/ ./backend/
COPY docs/ ./docs/
COPY --from=frontend /app/frontend/dist ./frontend/dist

RUN useradd --create-home --uid 10001 scai \
    && mkdir -p /app/backend/data /app/models \
    && chown -R scai:scai /app
USER scai

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

WORKDIR /app/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
