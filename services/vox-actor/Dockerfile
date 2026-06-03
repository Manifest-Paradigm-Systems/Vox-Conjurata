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

# 2. Pin numpy < 2.0.0 immediately to avoid compatibility issues with onnxruntime
#    We do this before any other pip installs.
RUN pip3 install --no-cache-dir "numpy<2.0.0"

# 3. Install CosyVoice from GitHub (clone for third_party/Matcha-TTS dependency)
RUN git clone --recurse-submodules https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
# Matcha-TTS is used via sys.path (not pip-install)
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS:$PYTHONPATH"

# 4. Install inference deps (skip deepspeed/gradio/whisper/grpcio — training only)
#    We use --extra-index-url for the ROCm WHL index to ensure Torch doesn't downgrade to CPU.
RUN grep -vE "deepspeed|whisper|gradio|extra-index-url|grpcio" /app/CosyVoice/requirements.txt > /tmp/cv-req.txt && \
    sed -i '/^transformers/d' /tmp/cv-req.txt && \
    pip3 install --no-cache-dir -r /tmp/cv-req.txt --extra-index-url https://download.pytorch.org/whl/rocm7.2/ && \
    pip3 install --no-cache-dir "huggingface-hub" "openai-whisper" "transformers<4.48" --extra-index-url https://download.pytorch.org/whl/rocm7.2/

# 5. Install server dependencies from requirements.txt
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# 6. Pre-download models? Done at runtime on first load via snapshot_download.
# The shared /models volume will cache them across restarts.

COPY app.py /app/app.py

EXPOSE 5020

CMD ["python3", "/app/app.py"]