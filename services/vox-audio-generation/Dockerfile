# vox-conjurata Audio Generation (Stable Audio 3 — music and SFX)
#
# BASE IS PINNED TO THE SAME ROCm THE HIGGS IMAGE USES, ON PURPOSE.
# `rocm/pytorch:latest` floats to ROCm 7.x, whose pip `_rocm_sdk_core` carries a
# userspace busy-poll — `rocr::core::Runtime::AsyncEventsLoop` — that pins a CPU
# core at 100% for as long as a model is resident. On this box that held the CCD
# at 78C whenever the music service was up. The rocm6.4 stack blocks in the
# kernel instead (`hsaKmtWaitOnMultipleEvents_Ext` -> hsakmt_ioctl) and Higgs
# runs with *zero* AsyncEventsLoop threads. Verified by gdb on both containers.
#
# With ROCm 6.4 the gfx1201 override is required again (ROCm 7 handled it
# natively, which is why the compose used to empty this): 12.0.1, as Higgs sets.
FROM docker.io/rocm/pytorch:rocm6.4.4_ubuntu24.04_py3.12_pytorch_release_2.7.1

ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app
# Ensure the render and video groups exist for Podman group_add
RUN groupadd -r render || true && groupadd -r video || true

# stable-audio-3 from git with --no-deps so it cannot pull a non-ROCm torch;
# the rest pinned per its pyproject. torchaudio/torchvision are re-installed
# from the rocm6.4 wheel index exactly as the Higgs image does, which lands
# torch 2.9.x+rocm6.4 rather than the base's 2.7.1.
RUN pip install --no-cache-dir "git+https://github.com/Stability-AI/stable-audio-3.git" --no-deps && \
    pip install --no-cache-dir einops einops-exts "numpy>=2.2.6" safetensors \
        "huggingface-hub>=1.7.1" "transformers>=5.8.0" soundfile tqdm \
        fastapi "uvicorn[standard]" scipy && \
    pip install --no-cache-dir --force-reinstall \
        --index-url https://download.pytorch.org/whl/rocm6.4 \
        "torchaudio>=2.9.0,<2.10.0" torchvision

# Copy application code
COPY tts_engine.py .

EXPOSE 8000

CMD ["python3", "tts_engine.py"]
