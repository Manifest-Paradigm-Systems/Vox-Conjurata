# vox-actor (CosyVoice AMD PyTorch Base Build)
FROM docker.io/rocm/pytorch:latest

# Architecture parameters for RDNA3/RDNA4
ENV HSA_OVERRIDE_GFX_VERSION=11.0.0
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Ensure video/render groups exist for Podman group_add mapping
RUN groupadd -r render || true && groupadd -r video || true

# 1. SYSTEM DEPS: runtime dependencies
RUN apt-get update && apt-get install -y \
    git curl ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 2. CLONE: Setup CosyVoice codebase
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
WORKDIR /app/CosyVoice

# 3. SANITIZE: Remove PyTorch and library locks to use pre-installed AMD binaries
RUN sed -i '/torch/d' requirements.txt && \
    sed -i '/torchaudio/d' requirements.txt && \
    sed -i '/onnxruntime/d' requirements.txt && \
    sed -i '/grpcio/d' requirements.txt && \
    sed -i '/openai-whisper/d' requirements.txt && \
    sed -i '/tensorrt/d' requirements.txt && \
    sed -i '/nvidia/d' requirements.txt && \
    sed -i 's/inflect==7.0.0/inflect>=7.0.0/' requirements.txt

# 4. Install dependencies
RUN pip3 install --no-cache-dir grpcio openai-whisper --no-build-isolation
RUN pip3 install --no-cache-dir -r requirements.txt
RUN pip3 install --no-cache-dir onnxruntime-rocm
RUN pip3 install --no-cache-dir fastapi uvicorn python-multipart modelscope loguru

# 5. PRE-DOWNLOAD MODELS
RUN python3 -c "from modelscope import snapshot_download; snapshot_download('pengzhendong/wetext', local_dir='/root/.cache/modelscope/hub/pengzhendong/wetext')"

# Environment setup
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS"
COPY app.py /app/app.py

EXPOSE 5020

CMD ["python3", "/app/app.py"]
