# vox-audio-core (VoxCPM2 + Pedalboard)
# Optimized for AMD ROCm / RDNA3 (gfx1201)
# Pinned to ROCm 6.4.2 for VoxCPM2 generation stability (7.x has NaN regression)
FROM docker.io/rocm/pytorch:rocm6.4.2_ubuntu22.04_py3.10_pytorch_release_2.6.0

ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV TORCH_BLAS_PREFER_HIPBLASLT=0

WORKDIR /app

# Ensure video/render groups exist
RUN groupadd -r render || true && groupadd -r video || true

# SYSTEM DEPS
RUN apt-get update && apt-get install -y \
    git curl ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install CUDA compat layer for torchaudio (ROCm 6.x ships without libcudart)
RUN pip3 install --no-cache-dir nvidia-cuda-runtime-cu12 && \
    ln -s /usr/local/lib/python3.10/site-packages/nvidia/cuda_runtime/lib/libcudart.so.12 /usr/lib/libcudart.so.13 2>/dev/null || \
    find / -name "libcudart*" -type f 2>/dev/null | head -5

# Pin numpy < 2.0.0
RUN pip3 install --no-cache-dir "numpy<2.0.0"

# Install dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

EXPOSE 8000

CMD ["python3", "main.py"]
