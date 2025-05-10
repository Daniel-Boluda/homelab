# Dockerfile
FROM ghcr.io/home-assistant/home-assistant:2025.5.1

# Parchear la restricción del endpoint en config_flow.py
RUN sed -i 's/if not host.endswith(".amazonaws.com"):/# \0/' /usr/src/homeassistant/homeassistant/components/aws_s3/config_flow.py && \
    sed -i 's/raise InvalidEndpointURL/# \0/' /usr/src/homeassistant/homeassistant/components/aws_s3/config_flow.py
