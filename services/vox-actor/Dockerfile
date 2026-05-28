# vox-actor (CosyVoice ROCm Hardened) Dockerfile
FROM docker.io/rocm/pytorch:latest

# Match host HSA version for RDNA3 (7900 XTX / 7800 XT)
ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git curl ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install basic dependencies and build tools
# We explicitly install setuptools and wheel to handle build-from-source requirements
RUN pip3 install --no-cache-dir setuptools wheel

# Clone CosyVoice
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
WORKDIR /app/CosyVoice

# Patch requirements to remove direct torch/torchaudio/onnxruntime references 
# and problematic pinned versions that might fail builds or pull in CUDA.
RUN sed -i '/torch/d' requirements.txt && \
    sed -i '/torchaudio/d' requirements.txt && \
    sed -i '/onnxruntime/d' requirements.txt && \
    sed -i '/grpcio/d' requirements.txt && \
    sed -i '/openai-whisper/d' requirements.txt && \
    sed -i 's/inflect==7.0.0/inflect>=7.0.0/' requirements.txt

# Pre-install problematic packages as binary wheels
RUN pip3 install --no-cache-dir grpcio openai-whisper --only-binary :all:

# Install remaining requirements
RUN pip3 install --no-cache-dir -r requirements.txt

# Install ROCm compatible onnxruntime
RUN pip3 install --no-cache-dir onnxruntime-rocm

# HARD BLOCKS: Explicitly block/remove any NVIDIA or TensorRT related packages
RUN pip3 list | grep -E "nvidia|tensorrt|cuda-python" | awk '{print $1}' | xargs -r pip3 uninstall -y

# Add CosyVoice and its third-party dependencies to PYTHONPATH
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS"

# Copy the application script
COPY app.py /app/app.py

EXPOSE 5020

# Run from the CosyVoice directory
CMD ["python3", "/app/app.py"]
