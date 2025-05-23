docker build -t pythonselenium -f Dockerfile .
docker tag pythonselenium bolferdocker/pythonselenium:1.0.4
docker push bolferdocker/pythonselenium:1.0.4
