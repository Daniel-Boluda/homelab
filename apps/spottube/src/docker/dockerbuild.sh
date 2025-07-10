docker build -t pythonmutagen -f Dockerfile .
docker tag pythonmutagen bolferdocker/pythonmutagen:0.0.0
docker push bolferdocker/pythonmutagen:0.0.0
