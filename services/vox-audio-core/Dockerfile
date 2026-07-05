# vox-audio-core (VoxCPM2 + Pedalboard)
# Optimized for AMD ROCm / RDNA3 (gfx1201)
FROM docker.io/rocm/pytorch:latest

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

# Pin numpy < 2.0.0
RUN pip3 install --no-cache-dir "numpy<2.0.0"

# Install dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

EXPOSE 8000

CMD ["python3", "main.py"]
