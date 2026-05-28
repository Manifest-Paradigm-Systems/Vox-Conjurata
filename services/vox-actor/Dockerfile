# vox-actor (CosyVoice) Dockerfile
FROM docker.io/rocm/dev-ubuntu-22.04:latest

ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y python3 python3-pip git curl && rm -rf /var/lib/apt/lists/*

# Install basic dependencies
RUN pip3 install --no-cache-dir fastapi uvicorn python-multipart torch torchaudio modelscope loguru

# Clone CosyVoice and install its specific requirements
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /app/CosyVoice
WORKDIR /app/CosyVoice
# Fix requirements for pip installation
RUN sed -i 's/inflect==7.0.0/inflect>=7.0.0/' requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# Add CosyVoice and its third-party dependencies to PYTHONPATH
ENV PYTHONPATH="/app/CosyVoice:/app/CosyVoice/third_party/Matcha-TTS"

# Copy the application script
COPY app.py /app/app.py

EXPOSE 5020

# Run from the CosyVoice directory to ensure relative paths work if any
CMD ["python3", "/app/app.py"]
