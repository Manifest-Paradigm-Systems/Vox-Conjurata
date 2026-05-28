FROM docker.io/rocm/dev-ubuntu-22.04:latest

ENV HSA_OVERRIDE_GFX_VERSION=10.3.0
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y python3 python3-pip git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Force the installation of PyTorch ROCm to block NVIDIA CUDA packages
RUN pip3 install --no-cache-dir torch torchvision torchaudio \
  --index-url https://download.pytorch.org/whl/rocm6.0/

RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8003

CMD ["python3", "app.py"]
