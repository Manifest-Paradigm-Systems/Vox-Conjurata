# vox-dia (Nari Labs Dia-1.6B test engine)
# ROCm 6.4.4 + PyTorch (same base as vox-audio-core) — AMD-only, gfx1201
FROM docker.io/rocm/pytorch:rocm6.4.4_ubuntu24.04_py3.12_pytorch_release_2.7.1

ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# deps first, then re-pin torchaudio from the ROCm index (pip pulls CUDA
# builds by default, which link libcudart and crash at import on AMD)
RUN pip3 install --no-cache-dir fastapi "uvicorn[standard]" pydantic soundfile descript-audio-codec huggingface-hub safetensors && \
    pip3 install --no-cache-dir --force-reinstall \
        --index-url https://download.pytorch.org/whl/rocm6.4 \
        "torchaudio>=2.9.0,<2.10.0"

# Dia package: pure Python — put on path directly (no hatchling build needed)
COPY dia/ /app/dia

# Service
COPY app/main.py /app/main.py

EXPOSE 8014

CMD ["python3", "main.py"]
