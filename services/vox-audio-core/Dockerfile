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

# Reinstall torchaudio built for ROCm (stock one links against libcudart)
RUN pip3 install --no-cache-dir --force-reinstall torchaudio --index-url https://download.pytorch.org/whl/rocm6.2

# Pin numpy < 2.0.0
RUN pip3 install --no-cache-dir "numpy<2.0.0"

# Install dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

EXPOSE 8000

CMD ["python3", "main.py"]
