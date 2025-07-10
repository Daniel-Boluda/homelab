docker build -t pythonmutagen -f src/docker/Dockerfile src/
docker tag pythonmutagen bolferdocker/pythonmutagen:0.0.0
docker push bolferdocker/pythonmutagen:0.0.0
