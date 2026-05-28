import os
from huggingface_hub import hf_hub_download

repo_id = "hum-ma/SDXL-models-GGUF"
local_dir = "/models"
hf_token = os.environ.get("HF_TOKEN")

files = [
    "stable-diffusion-xl-base-1.0-Q4_0.gguf",
    "clip/clip_l.safetensors",
    "clip/clip_g.safetensors",
    "vae/xlVAEC_c91.safetensors"
]

print(f"Using HF_TOKEN: {'Yes' if hf_token else 'No'}")

for filename in files:
    print(f"Downloading {filename}...")
    try:
        path = hf_hub_download(
            repo_id=repo_id, 
            filename=filename, 
            local_dir=local_dir, 
            local_dir_use_symlinks=False,
            token=hf_token
        )
        print(f"Saved to {path}")
    except Exception as e:
        print(f"Error downloading {filename}: {e}")
