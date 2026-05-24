# vox-conjurata Audio Generation Service Dockerfile
FROM docker.io/rocm/pytorch:latest

ENV HSA_OVERRIDE_GFX_VERSION=10.3.0

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir fastapi uvicorn

# Copy application code
COPY app.py .

EXPOSE 8000

CMD ["python", "app.py"]
