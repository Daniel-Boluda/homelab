docker build -t nvidia-watcher -f Dockerfile .
docker tag nvidia-watcher bolferdocker/nvidia-watcher:0.0.2
docker push bolferdocker/nvidia-watcher:0.0.2
