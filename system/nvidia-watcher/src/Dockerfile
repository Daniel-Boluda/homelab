FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY nvidia-watcher.py .

RUN apt-get update && apt-get install -y curl

CMD ["python", "nvidia-watcher.py"]