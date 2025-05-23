docker build -t pythonselenium -f Dockerfile .
docker tag pythonselenium bolferdocker/pythonselenium:1.0.5
docker push bolferdocker/pythonselenium:1.0.5
