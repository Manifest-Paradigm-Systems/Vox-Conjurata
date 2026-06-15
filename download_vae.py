import os
from huggingface_hub import hf_hub_download

repo_id = "madebyollin/sdxl-vae-fp16-fix"
filename = "sdxl_vae.safetensors"
local_dir = "/var/home/EvokeStudio/.local/share/containers/storage/volumes/vox-conjurata_model_storage/_data/vae"
target_filename = "sdxl_vae_fp16_fix.safetensors"

os.makedirs(local_dir, exist_ok=True)
print(f"Downloading {filename} from {repo_id}...")
try:
    path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=local_dir,
        token="hf_REDACTED"
    )
    
    target_path = os.path.join(local_dir, target_filename)
    if os.path.exists(target_path) and os.path.abspath(path) != os.path.abspath(target_path):
        os.remove(target_path)
    
    if os.path.abspath(path) != os.path.abspath(target_path):
        os.rename(path, target_path)
        print(f"Renamed VAE to {target_filename}")
        
    print(f"Download complete. VAE saved to {target_path}")
except Exception as e:
    print(f"Error downloading VAE: {e}")
    exit(1)
