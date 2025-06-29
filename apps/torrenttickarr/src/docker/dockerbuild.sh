docker build -t pythonplaywright -f Dockerfile .
docker tag pythonplaywright bolferdocker/pythonplaywright:1.0.6
docker push bolferdocker/pythonplaywright:1.0.6
