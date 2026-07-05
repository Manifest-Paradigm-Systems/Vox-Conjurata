# vox-audio-core (VoxCPM2 + Pedalboard)
# Optimized for AMD ROCm / RDNA3 (gfx1201)
FROM docker.io/rocm/pytorch:latest

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

# Pin numpy < 2.0.0
RUN pip3 install --no-cache-dir "numpy<2.0.0"

# Install CUDA 12 runtime for torchaudio (ROCm image lacks libcudart)
RUN wget -q https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb && \
    dpkg -i cuda-keyring_1.1-1_all.deb && \
    apt-get update && apt-get install -y cuda-libraries-12-6 && \
    rm -f cuda-keyring_1.1-1_all.deb && \
    rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/local/cuda-12.6/targets/x86_64-linux/lib/libcudart.so.12 /usr/lib/x86_64-linux-gnu/libcudart.so.13

# Install dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Pin torchao to version compatible with PyTorch 2.6.0
RUN pip3 install --no-cache-dir "torchao<0.14.0"

# Copy application code
COPY main.py .

EXPOSE 8000

CMD ["python3", "main.py"]
