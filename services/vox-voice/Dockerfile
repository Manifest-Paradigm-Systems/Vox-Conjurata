# Vox Voice - ROCm Python Environment (Whisper/StyleTTS2)
FROM rocm/dev-ubuntu-22.04:latest

# Set ROCm environment variables
ENV HSA_OVERRIDE_GFX_VERSION=10.3.0
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install Python and basic build tools
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# COPY requirements.txt .
# RUN pip3 install --no-cache-dir -r requirements.txt

EXPOSE 5000

CMD ["python3", "app.py"]
