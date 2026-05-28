# vox-designer (Parler-TTS) Dockerfile
FROM docker.io/rocm/dev-ubuntu-22.04:latest

ENV HSA_OVERRIDE_GFX_VERSION=10.3.0
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Install Parler-TTS from GitHub to ensure latest compatibility
RUN pip3 install --no-cache-dir git+https://github.com/huggingface/parler-tts.git
COPY app.py .

EXPOSE 5010

CMD ["python3", "app.py"]
