# vox-audio-core (VoxCPM2 + Pedalboard)
# Optimized for AMD ROCm / RDNA4 (gfx1201)
# ROCm 6.4.4 + PyTorch 2.7 — safe upgrade from 6.2.2 (NaN regression was ROCm 7.x only)
FROM docker.io/rocm/pytorch:rocm6.4.4_ubuntu24.04_py3.12_pytorch_release_2.7.1

ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Ensure video/render groups exist
RUN groupadd -r render || true && groupadd -r video || true

# SYSTEM DEPS
RUN apt-get update && apt-get install -y \
    git curl ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install application dependencies (excluding torch/torchcodec)
COPY requirements.txt .
RUN grep -vE '^(torch|torchcodec)' requirements.txt > req_fixed.txt && \
    pip3 install --no-cache-dir -r req_fixed.txt && \
    rm req_fixed.txt requirements.txt

# Install torchaudio from the ROCm index — pip pulls CUDA builds by default,
# which link against libcudart.so and crash at import.  The ROCm 6.4 index
# has torchaudio 2.9.1+rocm6.4 for cp312, compatible with torch 2.7.1.
RUN pip3 install --no-cache-dir --force-reinstall \
    --index-url https://download.pytorch.org/whl/rocm6.4 \
    "torchaudio>=2.9.0,<2.10.0"

# Copy application code
COPY main.py .

EXPOSE 8000

CMD ["python3", "main.py"]
