# vox-actor (CosyVoice 3 TTS Service)
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

# 2. Install CosyVoice from GitHub (clone for third_party/Matcha-TTS dependency)
RUN git clone --recurse-submodules https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
# Matcha-TTS is used via sys.path (not pip-install)
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS:$PYTHONPATH"

# 3. Install inference deps (skip deepspeed/gradio/whisper/grpcio/grpcio-tools — training only)
RUN grep -vE "deepspeed|whisper|gradio|extra-index-url|grpcio" /app/CosyVoice/requirements.txt > /tmp/cv-req.txt && \
    pip3 install --no-cache-dir -r /tmp/cv-req.txt && \
    pip3 install --no-cache-dir "huggingface-hub" "openai-whisper"

# 4. Install server dependencies
RUN pip3 install --no-cache-dir fastapi uvicorn[standard] python-multipart torchaudio soundfile

# 4. Pre-download models? Done at runtime on first load via snapshot_download.
# The shared /models volume will cache them across restarts.

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 5020

CMD ["python3", "/app/app.py"]