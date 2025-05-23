docker build -t pythonselenium -f Dockerfile .
docker tag pythonselenium bolferdocker/pythonselenium:1.0.3
docker push bolferdocker/pythonselenium:1.0.3
