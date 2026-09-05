# vox-higgs (Boson AI Higgs TTS 2 — Llama-3.2-3B backbone + DualFFN)
# ROCm 6.4.4 base, same family as vox-audio-core. LLM INT8; audio parts bf16.
FROM docker.io/rocm/pytorch:rocm6.4.4_ubuntu24.04_py3.12_pytorch_release_2.7.1

ENV HSA_OVERRIDE_GFX_VERSION=12.0.1
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip3 install --no-cache-dir "transformers>=5.3,<6" huggingface-hub soundfile

# ROCm-build workaround: stub the transformers.loss.loss_utils import chain
# (vision-loss modules abort this torch build); modeling_utils only needs
# LOSS_MAPPING from it, unused by Higgs.
COPY _higgs_fix.py /opt/conda/envs/py_3.12/lib/python3.12/site-packages/_higgs_fix.py
COPY _higgs_fix.pth /opt/conda/envs/py_3.12/lib/python3.12/site-packages/_higgs_fix.pth

COPY app/main.py /app/main.py

EXPOSE 8024

CMD ["python3", "main.py"]
