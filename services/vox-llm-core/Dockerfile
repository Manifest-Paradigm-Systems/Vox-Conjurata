# vox-llm-core — OpenRouter proxy server
# Replaces the local llama-server/Qwen with a lightweight FastAPI proxy
# that forwards requests to OpenRouter based on routing configuration.

FROM docker.io/python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy proxy code and routing config
COPY proxy.py .
COPY openrouter_routing.json .

EXPOSE 8080

ENTRYPOINT ["python", "proxy.py"]