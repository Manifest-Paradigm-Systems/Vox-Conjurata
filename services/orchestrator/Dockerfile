# Final vox-conjurata Orchestrator Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# BRUTE FORCE FIX FOR MULTIPART
RUN pip uninstall -y multipart python-multipart && pip install --no-cache-dir python-multipart

# Copy application code
COPY production_brain.py .

# Environment variables defaults
ENV OLLAMA_URL=http://vox-llm-core:11434
ENV FOUNDRY_API_URL=http://foundry-vtt:30000/api
ENV FOUNDRY_API_KEY=""

EXPOSE 8080

# Run the application
CMD ["uvicorn", "production_brain:app", "--host", "0.0.0.0", "--port", "8080"]
