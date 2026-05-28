# vox-actor (CosyVoice ROCm Clean-Slate) Dockerfile
FROM docker.io/rocm/dev-ubuntu-22.04:latest

# Match host HSA version for RDNA3 (7900 XTX / 7800 XT)
ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y \
    python3 python3-pip git curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# PYTORCH DIRECTIVE: Force ROCm 6.0 wheels
# Using explicit --index-url as requested to bypass default CUDA lookups
RUN pip3 install --no-cache-dir \
    torch torchaudio \
    --index-url https://download.pytorch.org/whl/rocm6.0

# Install basic dependencies (avoiding ones that might pull in CUDA)
RUN pip3 install --no-cache-dir \
    fastapi uvicorn python-multipart modelscope loguru

# Clone CosyVoice and setup environment
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
WORKDIR /app/CosyVoice

# Patch requirements to remove any potential NVIDIA/CUDA dependencies
# and fix the inflect version issue
RUN sed -i '/torch/d' requirements.txt && \
    sed -i '/torchaudio/d' requirements.txt && \
    sed -i '/onnxruntime/d' requirements.txt && \
    sed -i 's/inflect==7.0.0/inflect>=7.0.0/' requirements.txt

# Install remaining requirements
RUN pip3 install --no-cache-dir -r requirements.txt

# HARD BLOCKS: Explicitly block/remove any NVIDIA or TensorRT related packages
RUN pip3 install --no-cache-dir onnxruntime-rocm && \
    pip3 list | grep -E "nvidia|tensorrt|cuda-python" | awk '{print $1}' | xargs -r pip3 uninstall -y

# Add CosyVoice and its third-party dependencies to PYTHONPATH
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS"

# Copy the application script
COPY app.py /app/app.py

EXPOSE 5020

# Run from the CosyVoice directory
CMD ["python3", "/app/app.py"]
