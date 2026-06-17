FROM docker.io/rocm/dev-ubuntu-22.04:latest

ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y python3 python3-pip git cmake build-essential ninja-build && rm -rf /var/lib/apt/lists/*

# Install llama-cpp-python with ROCm support (forcing no cache to ensure compilation with hipcc)
RUN CMAKE_ARGS="-DGGML_HIPBLAS=ON -DCMAKE_C_COMPILER=hipcc -DCMAKE_CXX_COMPILER=hipcc -DAMDGPU_TARGETS=gfx1201" pip3 install --no-cache-dir llama-cpp-python[server]

EXPOSE 8000

# The command will be provided by compose.yaml
