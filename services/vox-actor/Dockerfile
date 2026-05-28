# vox-actor (CosyVoice ROCm Clean-Slate Hardened) Dockerfile
FROM docker.io/rocm/pytorch:latest

# Match host HSA version for RDNA3 (7900 XTX / 7800 XT)
ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 1. BASE SYSTEM: Install essential build and runtime dependencies
RUN apt-get update && apt-get install -y \
    git curl ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 2. PYTORCH DIRECTIVE: Force exact ROCm 6.0 versions to prevent symbol mismatches
RUN pip3 install --no-cache-dir --force-reinstall \
    torch==2.4.0 torchvision==0.19.0 torchaudio==2.4.0 \
    --index-url https://download.pytorch.org/whl/rocm6.0

# 3. BUILD TOOLS: Install legacy setuptools for compatibility
RUN pip3 install --no-cache-dir "setuptools<70.0.0" wheel

# 4. CLONE: Setup CosyVoice codebase
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
WORKDIR /app/CosyVoice

# 5. HARD BLOCKS: Aggressively sanitize requirements.txt
RUN sed -i '/torch/d' requirements.txt && \
    sed -i '/torchaudio/d' requirements.txt && \
    sed -i '/onnxruntime/d' requirements.txt && \
    sed -i '/grpcio/d' requirements.txt && \
    sed -i '/openai-whisper/d' requirements.txt && \
    sed -i '/tensorrt/d' requirements.txt && \
    sed -i '/nvidia/d' requirements.txt && \
    sed -i 's/inflect==7.0.0/inflect>=7.0.0/' requirements.txt

# 6. SOURCE DEPS: Install problematic packages using legacy build support
RUN pip3 install --no-cache-dir grpcio --only-binary :all: || pip3 install --no-cache-dir grpcio --no-build-isolation
RUN pip3 install --no-cache-dir openai-whisper --no-build-isolation

# 7. INSTALL: Remaining sanitized requirements
RUN pip3 install --no-cache-dir -r requirements.txt

# 8. ROCM ONNX: Install AMD-compatible execution provider
RUN pip3 install --no-cache-dir onnxruntime-rocm

# 9. BASIC APP DEPS
RUN pip3 install --no-cache-dir fastapi uvicorn python-multipart modelscope loguru

# 10. FINAL SANITIZATION: Explicitly uninstall any stealth CUDA leftovers
RUN pip3 list | grep -E "nvidia|tensorrt|cuda-python" | awk '{print $1}' | xargs -r pip3 uninstall -y

# Add CosyVoice and dependencies to path
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS"

# Copy the application script
COPY app.py /app/app.py

EXPOSE 5020

# Run from the CosyVoice directory
CMD ["python3", "/app/app.py"]
