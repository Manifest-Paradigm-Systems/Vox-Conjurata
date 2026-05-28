# vox-actor (CosyVoice ROCm) Dockerfile
FROM docker.io/rocm/dev-ubuntu-22.04:latest

# Match host HSA version for RDNA3 (7900 XTX / 7800 XT)
ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y \
    python3 python3-pip git curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch ROCm wheels directly
# Using 2.4.0+rocm6.1 as it is the closest stable version available for this index
RUN pip3 install --no-cache-dir \
    torch==2.4.0 torchaudio==2.4.0 \
    --index-url https://download.pytorch.org/whl/rocm6.1

# Install basic dependencies (avoiding ones that might pull in CUDA)
RUN pip3 install --no-cache-dir \
    fastapi uvicorn python-multipart modelscope loguru

# Clone CosyVoice
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
WORKDIR /app/CosyVoice

# Patch requirements to remove direct torch/torchaudio/onnxruntime references 
# that might pull in CUDA equivalents, then install.
RUN sed -i '/torch/d' requirements.txt && \
    sed -i '/torchaudio/d' requirements.txt && \
    sed -i '/onnxruntime/d' requirements.txt && \
    sed -i 's/inflect==7.0.0/inflect>=7.0.0/' requirements.txt

# Install remaining requirements
RUN pip3 install --no-cache-dir -r requirements.txt

# Install ROCm compatible onnxruntime
RUN pip3 install --no-cache-dir onnxruntime-rocm

# Final Purge of NVIDIA leftovers just in case
RUN pip3 list | grep nvidia | awk '{print $1}' | xargs -r pip3 uninstall -y

# Add CosyVoice and its third-party dependencies to PYTHONPATH
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS"

# Copy the application script
COPY app.py /app/app.py

EXPOSE 5020

# Run from the CosyVoice directory
CMD ["python3", "/app/app.py"]
