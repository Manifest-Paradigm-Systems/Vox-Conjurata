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

# Patch load_audio to use soundfile directly.
# torchaudio 2.10+rocm routes torchaudio.load() through torchcodec even when
# backend="soundfile" is specified, and torchcodec is not available in this ROCm
# environment. soundfile (libsndfile1) is already installed and handles WAV correctly.
RUN python3 - << 'PYEOF'
path = "/opt/fish-speech/fish_speech/inference_engine/reference_loader.py"
with open(path) as f:
    lines = f.readlines()

# Find the load_audio method start
start = next(i for i, l in enumerate(lines) if "def load_audio(self, reference_audio, sr):" in l)

new_block = [
    "    def load_audio(self, reference_audio, sr):\n",
    "        \"\"\"\n",
    "        Load audio from file path or raw bytes.\n",
    "        Patched: uses soundfile directly to bypass torchaudio torchcodec\n",
    "        dependency unavailable in this ROCm environment (torchaudio 2.10+).\n",
    "        \"\"\"\n",
    "        import io as _io\n",
    "        import soundfile as _sf\n",
    "        import torch as _torch\n",
    "\n",
    "        if isinstance(reference_audio, (str, Path)):\n",
    "            src = str(reference_audio)\n",
    "        elif isinstance(reference_audio, (bytes, bytearray)):\n",
    "            src = _io.BytesIO(reference_audio)\n",
    "        else:\n",
    "            src = reference_audio  # BytesIO or file-like\n",
    "\n",
    "        audio_np, original_sr = _sf.read(src, dtype=\"float32\", always_2d=True)\n",
    "        # soundfile: (samples, channels) -> torch needs (channels, samples)\n",
    "        waveform = _torch.from_numpy(audio_np.T)\n",
    "\n",
    "        if waveform.shape[0] > 1:\n",
    "            waveform = _torch.mean(waveform, dim=0, keepdim=True)\n",
    "\n",
    "        if original_sr != sr:\n",
    "            resampler = torchaudio.transforms.Resample(\n",
    "                orig_freq=original_sr, new_freq=sr\n",
    "            )\n",
    "            waveform = resampler(waveform)\n",
    "\n",
    "        audio = waveform.squeeze().numpy()\n",
    "        return audio\n",
]

new_lines = lines[:start] + new_block
with open(path, "w") as f:
    f.writelines(new_lines)
print("load_audio patched successfully")
PYEOF

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
