# vox-conjurata Audio Generation (TTS & Stable Audio) Dockerfile
FROM docker.io/rocm/pytorch:latest

ENV HSA_OVERRIDE_GFX_VERSION=11.0.0
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app
# Ensure the render and video groups exist for Podman group_add
RUN groupadd -r render || true && groupadd -r video || true

# Install dependencies (fastapi, uvicorn, diffusers, transformers, accelerate, scipy, edge-tts, torchsde)
# stable-audio-3 native stack (2026-09-09): lib from git (--no-deps so the
# rocm/pytorch base's ROCm torch is kept), rest pinned per its pyproject.
RUN pip install --no-cache-dir "git+https://github.com/Stability-AI/stable-audio-3.git" --no-deps && \
    pip install --no-cache-dir einops einops-exts "numpy>=2.2.6" safetensors \
        "huggingface-hub>=1.7.1" "transformers>=5.8.0" soundfile tqdm \
        fastapi "uvicorn[standard]" scipy

# Copy application code
COPY tts_engine.py .

EXPOSE 8000

CMD ["python3", "tts_engine.py"]
