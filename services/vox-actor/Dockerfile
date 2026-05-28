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
# Purging any potential NVIDIA/CUDA indices by using explicit --index-url
RUN pip3 install --no-cache-dir \
    torch torchaudio \
    --index-url https://download.pytorch.org/whl/rocm6.1

# Install basic dependencies
RUN pip3 install --no-cache-dir \
    fastapi uvicorn python-multipart modelscope loguru

# Clone CosyVoice and install its specific requirements
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
WORKDIR /app/CosyVoice

# Fix requirements: remove specific versions that might force CUDA/NVIDIA deps
# and ensure compatible versions for ROCm environment.
RUN sed -i 's/inflect==7.0.0/inflect>=7.0.0/' requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# Explicitly ensure NO NVIDIA packages are present
RUN pip3 uninstall -y tensorrt-llm cuda-python nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12

# Add CosyVoice and its third-party dependencies to PYTHONPATH
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS"

# Copy the application script
COPY app.py /app/app.py

EXPOSE 5020

# Run from the CosyVoice directory
CMD ["python3", "/app/app.py"]
