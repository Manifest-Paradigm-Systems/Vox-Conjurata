# Stage 1: Get fish-speech source code from the official image
FROM docker.io/fishaudio/fish-speech:v1.5.1 AS source

# Stage 2: Build the ROCm container using AMD's PyTorch image
FROM docker.io/rocm/pytorch:latest

# Set environment variables for ROCm and PyTorch
ENV DEBIAN_FRONTEND=noninteractive
ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV PYTHONPATH=/opt/fish-speech

# Install libsndfile1 (required by soundfile) and portaudio19-dev (required by pyaudio)
RUN apt-get update && apt-get install -y libsndfile1 git portaudio19-dev && rm -rf /var/lib/apt/lists/*

# Copy the fish-speech codebase from the source stage
COPY --from=source /opt/fish-speech /opt/fish-speech

# Patch torchaudio list_audio_backends deprecation/removal in newer TorchAudio versions
RUN sed -i 's/torchaudio.list_audio_backends()/getattr(torchaudio, "list_audio_backends", lambda: ["soundfile"])()/g' /opt/fish-speech/fish_speech/inference_engine/reference_loader.py

# Set working directory to the fish-speech code
WORKDIR /opt/fish-speech

# Create /app directory to match the original checkpoints volume mount point
RUN mkdir -p /app/checkpoints

# Install dependencies (since torch is already present, this only installs non-torch dependencies)
RUN pip3 install --no-cache-dir -e .

# Expose API server port
EXPOSE 7860

# Set entrypoint and command matching original fish-speech
ENTRYPOINT ["python", "-m", "tools.api_server"]
CMD ["--listen", "0.0.0.0:7860", "--half"]
