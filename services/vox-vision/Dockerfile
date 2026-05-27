# vox-conjurata Vision Service Dockerfile
FROM docker.io/rocm/pytorch:latest

ENV HSA_OVERRIDE_GFX_VERSION=11.0.0
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install dependencies (fastapi, uvicorn, diffusers, transformers, accelerate)
RUN pip install --no-cache-dir fastapi uvicorn diffusers transformers accelerate

# Copy application code
COPY main.py .

EXPOSE 7860

CMD ["python", "main.py"]
