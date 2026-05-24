# vox-conjurata Orchestrator Service Dockerfile
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final Stage
FROM python:3.11-slim

LABEL project="vox-conjurata"
LABEL component="orchestrator"

WORKDIR /app

# Copy installed dependencies
COPY --from=builder /install /usr/local

# Copy application code
COPY main.py .

# Environment variables defaults
ENV OLLAMA_URL=http://vox-llm-core:11434
ENV FOUNDRY_API_URL=http://foundry-vtt:30000/api
ENV FOUNDRY_API_KEY=""

EXPOSE 8080

# Run the application
CMD ["uvicorn", "main.py:app", "--host", "0.0.0.0", "--port", "8080"]
