# Imagem oficial Microsoft Playwright pra Python — Chromium + libs já instalados.
# Atualizar a versão de tempos em tempos pra acompanhar playwright>=1.40.
FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/bugfinder.db

WORKDIR /app

# Instala dependências primeiro pra aproveitar cache de layer
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

# Volume montado em /data pelo Railway pra persistir DB + token cache
RUN mkdir -p /data

# Roda o loop de scans 24/7 — `watch` lê toda config do .env / env vars
CMD ["bugfinder", "watch"]
