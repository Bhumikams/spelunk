# Spelunk: one small container (fits Render's free 512 MB tier in read-only mode)

# ---- 1. Build the React frontend ---------------------------------------------
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. Python API + built frontend -------------------------------------------
FROM python:3.12-slim

# git is needed to clone repositories when indexing is enabled
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 user
WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Download the embedding model at build time so the first query is fast
ENV EMBED_CACHE_DIR=/app/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/app/models')"

COPY backend/ ./backend
COPY --from=web /web/dist ./static

ENV STATIC_DIR=/app/static \
    STORE=atlas \
    READ_ONLY=true \
    PYTHONUNBUFFERED=1

RUN chown -R user:user /app
USER user
WORKDIR /app/backend

EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-10000}"]
