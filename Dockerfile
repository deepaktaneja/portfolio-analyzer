# Stage 10 — reproducible container image (multi-stage, non-root, healthcheck).
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

# Copy installed dependencies from the builder stage
COPY --from=builder /install /usr/local

# Non-root runtime user
RUN useradd --create-home --shell /bin/bash appuser

COPY . .
RUN mkdir -p data logs data/backups && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=3).status==200 else sys.exit(1)" || exit 1

ENTRYPOINT ["bash", "docker/entrypoint.sh"]
