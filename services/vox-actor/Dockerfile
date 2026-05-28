# vox-actor (CosyVoice ROCm Clean-Slate Build)
FROM docker.io/rocm/dev-ubuntu-22.04:latest

# Architecture parameters for RDNA3
ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 1. SYSTEM DEPS: Essential build and runtime dependencies
RUN apt-get update && apt-get install -y \
    python3 python3-pip git curl ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 2. HARD BLOCKS: Explicitly block and remove any pip installation lines referencing NVIDIA/TensorRT
RUN pip3 install --no-cache-dir setuptools wheel && \
    pip3 list | grep -E "nvidia|tensorrt|cuda-python" | awk '{print $1}' | xargs -r pip3 uninstall -y

# 3. PYTORCH DIRECTIVE: Force native ROCm PyTorch wheels with explicit index
# As requested: pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm6.0/
RUN pip3 install --no-cache-dir --upgrade \
    torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/rocm6.0/

# 4. CLONE: Setup CosyVoice codebase
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
WORKDIR /app/CosyVoice

# 5. SANITIZE: Aggressively strip CUDA/NVIDIA deps from source requirements
RUN sed -i '/torch/d' requirements.txt && \
    sed -i '/torchaudio/d' requirements.txt && \
    sed -i '/onnxruntime/d' requirements.txt && \
    sed -i '/grpcio/d' requirements.txt && \
    sed -i '/openai-whisper/d' requirements.txt && \
    sed -i '/tensorrt/d' requirements.txt && \
    sed -i '/nvidia/d' requirements.txt && \
    sed -i 's/inflect==7.0.0/inflect>=7.0.0/' requirements.txt

# 6. SOURCE DEPS: Install problematic packages with legacy build support
RUN pip3 install --no-cache-dir grpcio openai-whisper --no-build-isolation

# 7. INSTALL: Remaining sanitized requirements
RUN pip3 install --no-cache-dir -r requirements.txt

# 8. ROCM ONNX: AMD-compatible execution provider
RUN pip3 install --no-cache-dir onnxruntime-rocm

# 9. BASIC APP DEPS
RUN pip3 install --no-cache-dir fastapi uvicorn python-multipart modelscope loguru

# 10. FINAL PURGE: Hard block verification
RUN pip3 list | grep -E "nvidia|tensorrt|cuda-python" | awk '{print $1}' | xargs -r pip3 uninstall -y

# Environment setup
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS"
COPY app.py /app/app.py

EXPOSE 5020

# Run from the CosyVoice directory
CMD ["python3", "/app/app.py"]
