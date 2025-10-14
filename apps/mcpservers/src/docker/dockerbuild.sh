docker build -t fastmcp-base -f Dockerfile .
docker tag fastmcp-base bolferdocker/fastmcp-base:0.0.5
docker push bolferdocker/fastmcp-base:0.0.5
