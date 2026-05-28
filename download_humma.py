import os
from huggingface_hub import hf_hub_download

repo_id = "hum-ma/SDXL-models-GGUF"
local_dir = "/models"

files = [
    "sdxl_base_Q4_0.gguf",
    "clip_l.safetensors",
    "clip_g.safetensors",
    "xlVAEC_c91.safetensors"
]

for filename in files:
    print(f"Downloading {filename}...")
    try:
        path = hf_hub_download(repo_id=repo_id, filename=filename, local_dir=local_dir, local_dir_use_symlinks=False)
        print(f"Saved to {path}")
    except Exception as e:
        print(f"Error downloading {filename}: {e}")
