docker build -t fastmcp-base -f Dockerfile .
docker tag fastmcp-base bolferdocker/fastmcp-base:0.0.6
docker push bolferdocker/fastmcp-base:0.0.6
