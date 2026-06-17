import os
from huggingface_hub import hf_hub_download

repo_id = "Green-Sky/CyberRealisticPony-GGUF"
filename = "CyberRealisticPony_V12.7-vae_f16-q4_0.gguf"
local_dir = "/var/home/EvokeStudio/.local/share/containers/storage/volumes/vox-conjurata_model_storage/_data"

print(f"Downloading {filename} from {repo_id}...")
try:
    path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=local_dir,
        token="hf_REDACTED"
    )
    
    # Standardize filename for compose.yaml mapping if needed, 
    # but I will update compose.yaml to point to this exact name for clarity.
    print(f"Download complete. Model saved to {path}")
except Exception as e:
    print(f"Error downloading model: {e}")
    exit(1)
