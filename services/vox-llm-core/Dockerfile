# vox-llm-core (llama-server compiled natively for AMD ROCm gfx1201)
FROM docker.io/rocm/dev-ubuntu-24.04:latest

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/rocm/bin:${PATH}"

# Install build dependencies
RUN apt-get update && apt-get install -y \
    git build-essential cmake libcurl4-openssl-dev hipblas-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone https://github.com/ggerganov/llama.cpp.git && \
    cd llama.cpp && \
    HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)" \
    cmake -B build -DGGML_HIP=ON -DCMAKE_HIP_ARCHITECTURES=gfx1201 -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=/opt/rocm && \
    cmake --build build --config Release --target llama-server -j$(nproc)

RUN cp -P /build/llama.cpp/build/bin/* /usr/local/bin/ 2>/dev/null || true && \
    find /build/llama.cpp/build -name "*.so*" -exec cp -d {} /usr/local/lib/ \; && \
    ldconfig && \
    rm -rf /build

EXPOSE 8080

ENTRYPOINT ["llama-server"]
