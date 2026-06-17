FROM docker.io/rocm/pytorch:latest

ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# PyTorch image already has ROCm/HIP environment variables set.
# We just need to install llama-cpp-python with the right flags.
RUN apt-get update && apt-get install -y cmake ninja-build && rm -rf /var/lib/apt/lists/*

# Install llama-cpp-python with HIP support
# Newer versions of llama.cpp (which llama-cpp-python uses) use GGML_HIP=on
RUN CMAKE_ARGS="-DGGML_HIP=on -DAMDGPU_TARGETS=gfx1201" FORCE_CMAKE=1 \
    pip3 install --no-cache-dir llama-cpp-python[server]

EXPOSE 8000
