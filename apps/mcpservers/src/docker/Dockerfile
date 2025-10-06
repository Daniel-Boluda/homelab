# ---- Dockerfile ----
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tini && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip && \
    pip install fastmcp fastapi

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 18000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "server.py"]
