docker build -t fastmcp-base -f Dockerfile .
docker tag fastmcp-base bolferdocker/fastmcp-base:0.0.1
docker push bolferdocker/fastmcp-base:0.0.1
