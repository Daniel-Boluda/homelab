docker build -t homeassistant-minio-patched -f Dockerfile .
docker tag homeassistant-minio-patched bolferdocker/homeassistant-minio-patched:2025.5.1
docker push bolferdocker/homeassistant-minio-patched:2025.5.1
