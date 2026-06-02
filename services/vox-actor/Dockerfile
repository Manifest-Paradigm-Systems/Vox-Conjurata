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

# 2. Install inference-only Python deps (pre-build grpcio binary)
RUN pip3 install --no-cache-dir grpcio
# Install CosyVoice requirements.txt (skip deepspeed — training only)
RUN pip3 install --no-cache-dir -r /app/CosyVoice/requirements.txt || \
    (grep -vE "deepspeed|whisper|gradio" /app/CosyVoice/requirements.txt > /tmp/cv-req.txt && \
     pip3 install --no-cache-dir -r /tmp/cv-req.txt)
# Ensure key inference deps are present
RUN pip3 install --no-cache-dir "huggingface-hub" "openai-whisper"
# Note: deepspeed (training), grpcio interoperability (training), and
# whisper/gradio are intentionally skipped — not needed for inference.

# 3. Install CosyVoice from GitHub (clone for third_party/Matcha-TTS dependency)
RUN git clone --recurse-submodules https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
# Matcha-TTS is used via sys.path (not pip-install) — same pattern as CosyVoice example.py.
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS:$PYTHONPATH"

# 4. Install server dependencies
RUN pip3 install --no-cache-dir fastapi uvicorn[standard] python-multipart torchaudio soundfile

# 4. Pre-download models? Done at runtime on first load via snapshot_download.
# The shared /models volume will cache them across restarts.

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 5020

CMD ["python3", "/app/app.py"]