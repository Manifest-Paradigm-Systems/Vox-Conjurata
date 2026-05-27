# vox-conjurata Audio Generation (TTS & Stable Audio) Dockerfile
FROM docker.io/rocm/pytorch:latest

ENV HSA_OVERRIDE_GFX_VERSION=11.0.0
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install dependencies (fastapi, uvicorn, diffusers, transformers, accelerate, scipy, edge-tts)
RUN pip install --no-cache-dir fastapi uvicorn diffusers transformers accelerate scipy edge-tts

# Copy application code
COPY tts_engine.py .

EXPOSE 8000

CMD ["python3", "tts_engine.py"]
