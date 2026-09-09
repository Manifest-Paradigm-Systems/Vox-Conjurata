FROM docker.io/rocm/dev-ubuntu-22.04:latest

ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# hipblas/rocblas NOT apt-installed: rocm/dev-ubuntu ships them in /opt/rocm
# (and the ROCm apt repo is frequently unreachable — 2026-09-09 outage).
RUN apt-get update && apt-get install -y python3 python3-pip git cmake build-essential ninja-build libomp-dev && rm -rf /var/lib/apt/lists/*

# Ensure the render and video groups exist for Podman group_add
RUN groupadd -r render || true && groupadd -r video || true

COPY requirements.txt .

# Install stable-diffusion-cpp-python# Explicitly compile against AMD ROCm (gfx1201)
RUN CMAKE_ARGS="-DSD_HIPBLAS=ON -DCMAKE_C_COMPILER=hipcc -DCMAKE_CXX_COMPILER=hipcc -DAMDGPU_TARGETS=gfx1201" pip3 install --no-cache-dir stable-diffusion-cpp-python

# Install the rest of the dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py .

ENV LD_LIBRARY_PATH=/opt/rocm/lib:/usr/lib/llvm-14/lib:$LD_LIBRARY_PATH

EXPOSE 8003

CMD ["python3", "app.py"]
