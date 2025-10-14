# ---- Dockerfile ----
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tini build-essential libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip && \
    pip install fastmcp fastapi asyncpg

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 18000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "server.py"]
