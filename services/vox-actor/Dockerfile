# vox-actor (OpenVoice V2 AMD PyTorch Base Build)
FROM docker.io/rocm/pytorch:latest

# Architecture parameters for RDNA3/RDNA4
ENV HSA_OVERRIDE_GFX_VERSION=11.0.0
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Ensure video/render groups exist for Podman group_add mapping
RUN groupadd -r render || true && groupadd -r video || true

# 1. SYSTEM DEPS: runtime dependencies
RUN apt-get update && apt-get install -y \
    git curl ffmpeg libsndfile1 espeak-ng mecab libmecab-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. CLONE MeloTTS and remove old transformers lock
RUN git clone https://github.com/myshell-ai/MeloTTS.git /app/MeloTTS
WORKDIR /app/MeloTTS
RUN sed -i 's/transformers==4.27.4/transformers>=4.40.0/g' requirements.txt

# 3. Pre-install newer tokenizers, transformers, and numpy to bypass Python 3.12 build issues
RUN pip3 install --no-cache-dir "tokenizers>=0.19.0" "transformers>=4.40.0" "numpy>=1.24.0" "setuptools<80.0.0"

# 4. Install MeloTTS from local modified directory
RUN pip3 install --no-cache-dir .

# 5. Clone and install OpenVoice in editable mode (relaxing strict old dependencies)
RUN git clone https://github.com/myshell-ai/OpenVoice.git /app/OpenVoice
WORKDIR /app/OpenVoice
RUN sed -i "s/'numpy==1.22.0'/'numpy>=1.22.0'/g" setup.py && \
    sed -i "s/'librosa==0.9.1'/'librosa>=0.9.1'/g" setup.py && \
    sed -i "s/'gradio==3.48.0'/'gradio>=3.48.0'/g" setup.py && \
    sed -i "s/'faster-whisper==0.9.0'/'faster-whisper>=0.9.0'/g" setup.py && \
    sed -i "s/'wavmark==0.0.3'/'wavmark>=0.0.3'/g" setup.py && \
    sed -i "s/'whisper-timestamped==1.14.2'/'whisper-timestamped>=1.14.2'/g" setup.py && \
    pip3 install --no-cache-dir -e .

# 6. Install additional server dependencies
RUN pip3 install --no-cache-dir fastapi uvicorn[standard] python-multipart pydantic loguru soundfile

# 7. Pre-download NLTK data and Unidic required by MeloTTS
RUN python3 -m unidic download
RUN python3 -c "import nltk; \
nltk.download('averaged_perceptron_tagger', quiet=True); \
nltk.download('averaged_perceptron_tagger_eng', quiet=True); \
nltk.download('cmudict', quiet=True)"

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 5020

CMD ["python3", "/app/app.py"]
