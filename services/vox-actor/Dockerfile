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

# 2. Install inference-only Python deps (skip training-only packages)
RUN pip3 install --no-cache-dir grpcio  # prebuild binary to avoid compile from source
RUN pip3 install --no-cache-dir \
    "conformer" \
    "diffusers" \
    "hydra-core" \
    "HyperPyYAML" \
    "inflect" \
    "librosa" \
    "lightning" \
    "matplotlib" \
    "modelscope" \
    "numpy" \
    "omegaconf" \
    "onnx" \
    "onnxruntime" \
    "protobuf" \
    "pyarrow" \
    "pydantic" \
    "pyworld" \
    "rich" \
    "scipy" \
    "sentencepiece" \
    "tensorboard" \
    "tensorboardx" \
    "tokenizers" \
    "transformers" \
    "huggingface-hub" \
    "openai-whisper"
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