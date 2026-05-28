# =============================================================================
# vox-actor — CosyVoice / FishSpeech TTS Engine
# PLATFORM:  AMD ROCm (RDNA3 / gfx1201)
# BASE:      Official AMD ROCm development base — Ubuntu 24.04
# =============================================================================
# HARD RULE: NO NVIDIA. NO TensorRT. NO cuda-python. NO nvidia-* packages.
#            Any stray CUDA dependency is a build failure.
# =============================================================================

FROM docker.io/rocm/dev-ubuntu-24.04:latest

# ---------------------------------------------------------------------------
# Environment — ROCm / AMD target configuration
# ---------------------------------------------------------------------------
ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV ROCR_VISIBLE_DEVICES=0
ENV HIP_VISIBLE_DEVICES=0
# Force HIP to target RDNA3 (gfx1201 / RX 9070 XT class)
ENV PYTORCH_ROCM_ARCH=gfx1201
# Prevent HIP from defaulting to any CUDA compat layer
ENV HIP_PLATFORM=amd
# Keep Python stdout flushed (useful for Podman logs)
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ---------------------------------------------------------------------------
# 1. SYSTEM DEPS — build tools, audio libs, Python
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev \
        git curl wget \
        ffmpeg libsndfile1 libsndfile1-dev \
        build-essential cmake pkg-config \
        # Audio / signal processing runtime libs
        libasound2-dev portaudio19-dev \
        # Needed by CosyVoice text-frontend
        libmecab-dev mecab mecab-ipadic-utf8 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 2. HARD BLOCK — purge any NVIDIA/TensorRT/CUDA artifacts that may have
#    leaked in from the base image layer.  This runs before ANY pip work.
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir setuptools wheel && \
    echo "=== [HARD BLOCK] Scanning for forbidden NVIDIA/TensorRT/CUDA packages ===" && \
    pip3 list --format=columns 2>/dev/null \
        | grep -iE "^(nvidia|tensorrt|cuda-python|tensorrt-llm)" \
        | awk '{print $1}' \
        | xargs -r pip3 uninstall -y && \
    echo "=== [HARD BLOCK] Forbidden packages cleared ==="

# ---------------------------------------------------------------------------
# 3. PYTORCH DIRECTIVE — native ROCm wheels from the official AMD index.
#    Install torch + Triton ROCm compiler together as one atomic step.
#    Index: https://download.pytorch.org/whl/rocm6.0/
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir --upgrade \
        torch torchvision torchaudio \
        --index-url https://download.pytorch.org/whl/rocm6.0/ && \
    echo "=== [PYTORCH] ROCm wheels installed ===" && \
    python3 -c "import torch; print('torch:', torch.__version__); print('ROCm available:', torch.cuda.is_available())"

# Force-install the ROCm-native Triton compiler (matches the torch ROCm build)
RUN pip3 install --no-cache-dir \
        "triton>=2.1.0" \
        --index-url https://download.pytorch.org/whl/rocm6.0/ || \
    pip3 install --no-cache-dir "triton>=2.1.0" && \
    echo "=== [TRITON] ROCm Triton compiler installed ==="

# ---------------------------------------------------------------------------
# 4. CLONE CosyVoice (with all submodules — Matcha-TTS etc.)
# ---------------------------------------------------------------------------
RUN git clone --recursive --depth=1 \
        https://github.com/FunAudioLLM/CosyVoice.git \
        /app/CosyVoice

WORKDIR /app/CosyVoice

# ---------------------------------------------------------------------------
# 5. SANITIZE — strip every CUDA/NVIDIA/TensorRT line from CosyVoice's own
#    requirements before we touch them.
# ---------------------------------------------------------------------------
RUN echo "=== [SANITIZE] Stripping NVIDIA/CUDA/TensorRT deps from upstream requirements ===" && \
    sed -i \
        -e '/^\s*torch[^a-z]/d' \
        -e '/^\s*torchvision/d' \
        -e '/^\s*torchaudio/d' \
        -e '/tensorrt/Id' \
        -e '/tensorrt-llm/Id' \
        -e '/cuda-python/Id' \
        -e '/nvidia/Id' \
        -e '/^\s*onnxruntime\b/d' \
        -e '/^\s*grpcio\b/d' \
        -e '/^\s*openai-whisper/d' \
        -e 's/inflect==7\.0\.0/inflect>=7.0.0/' \
    requirements.txt && \
    echo "=== [SANITIZE] Done — remaining requirements:" && \
    cat requirements.txt

# ---------------------------------------------------------------------------
# 6. SOURCE DEPS — packages that need --no-build-isolation for legacy builds
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir grpcio openai-whisper --no-build-isolation

# ---------------------------------------------------------------------------
# 7. COSYVOICE REQUIREMENTS — install sanitized upstream deps
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# 8. ONNX — ROCm execution provider (replaces CPU onnxruntime)
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir onnxruntime-rocm && \
    echo "=== [ONNX] onnxruntime-rocm installed ==="

# ---------------------------------------------------------------------------
# 9. SERVICE LAYER — FastAPI wrapper + utilities
# ---------------------------------------------------------------------------
RUN pip3 install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        python-multipart \
        modelscope \
        loguru \
        soundfile \
        numpy

# ---------------------------------------------------------------------------
# 10. FINAL HARD-BLOCK VERIFICATION — assert no forbidden packages remain
# ---------------------------------------------------------------------------
RUN echo "=== [FINAL VERIFICATION] Asserting zero NVIDIA/TensorRT/CUDA packages ===" && \
    FORBIDDEN=$(pip3 list --format=columns 2>/dev/null \
        | grep -iE "^(nvidia|tensorrt|cuda-python|tensorrt-llm)" \
        | awk '{print $1}') && \
    if [ -n "$FORBIDDEN" ]; then \
        echo "BUILD FAILURE: Forbidden packages found: $FORBIDDEN" && exit 1; \
    else \
        echo "PASS: No forbidden packages detected."; \
    fi

# ---------------------------------------------------------------------------
# Python path — expose CosyVoice and its Matcha-TTS submodule
# ---------------------------------------------------------------------------
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS"

# ---------------------------------------------------------------------------
# Copy application entrypoint
# ---------------------------------------------------------------------------
COPY app.py /app/app.py

EXPOSE 5020

# Run from repo root so CosyVoice relative imports resolve correctly
WORKDIR /app/CosyVoice
CMD ["python3", "/app/app.py"]
