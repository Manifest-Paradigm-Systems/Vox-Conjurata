# vox-actor (CosyVoice 3 TTS Service)
# Optimized for AMD ROCm / RDNA3 (gfx1201)
FROM docker.io/rocm/pytorch:latest

# Architecture parameters for RDNA3/RDNA4
ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Ensure video/render groups exist for Podman group_add mapping
RUN groupadd -r render || true && groupadd -r video || true

# 1. SYSTEM DEPS
RUN apt-get update && apt-get install -y \
    git curl ffmpeg libsndfile1 espeak-ng \
    && rm -rf /var/lib/apt/lists/*

# 2. Replace the base image's CUDA PyTorch with the ROCm variant.
#    The rocm/pytorch image ships ROCm 7.2, so we must match that index.
RUN pip3 install --no-cache-dir --force-reinstall \
    --index-url https://download.pytorch.org/whl/rocm7.2/ \
    torch torchvision torchaudio

# 3. Install CosyVoice from GitHub (clone for third_party/Matcha-TTS dependency)
RUN git clone --recurse-submodules https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
# Matcha-TTS is used via sys.path (not pip-install)
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS:$PYTHONPATH"

# 4. Install inference deps (skip deepspeed/gradio/whisper/grpcio — training only)
RUN grep -vE "deepspeed|whisper|gradio|extra-index-url|grpcio" /app/CosyVoice/requirements.txt > /tmp/cv-req.txt && \
    sed -i '/^transformers/d' /tmp/cv-req.txt && \
    pip3 install --no-cache-dir -r /tmp/cv-req.txt && \
    pip3 install --no-cache-dir "huggingface-hub" "openai-whisper" "transformers<4.48"

# 5. Install server dependencies from requirements.txt
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# 6. Pre-download models? Done at runtime on first load via snapshot_download.
# The shared /models volume will cache them across restarts.

COPY app.py /app/app.py

EXPOSE 5020

CMD ["python3", "/app/app.py"]