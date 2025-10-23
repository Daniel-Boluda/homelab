FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tini build-essential gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade pip && \
    pip install \
        fastmcp \
        fastapi \
        cloud-sql-python-connector[pg8000] \
        anyio \
        uvicorn \
        google-cloud-bigquery \
        google-cloud-bigquery-storage \
        pandas \
        pyarrow \
        db-dtypes

RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 18000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "server.py"]
