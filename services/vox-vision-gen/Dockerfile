FROM docker.io/rocm/dev-ubuntu-22.04:latest

ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y python3 python3-pip git cmake build-essential && rm -rf /var/lib/apt/lists/*

# Ensure the render and video groups exist for Podman group_add
RUN groupadd -r render || true && groupadd -r video || true

COPY requirements.txt .

# Install stable-diffusion-cpp-python natively compiled for ROCm AMD architecture
RUN CMAKE_ARGS="-DGGML_HIPBLAS=ON -DAMDGPU_TARGETS=gfx1201" pip3 install --no-cache-dir stable-diffusion-cpp-python

# Install the rest of the dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8003

CMD ["python3", "app.py"]
