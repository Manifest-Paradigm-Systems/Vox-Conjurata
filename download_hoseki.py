import os
from huggingface_hub import hf_hub_download

repo_id = "void-gryph/hoseki-lustrousmix-pony-xl-GGUF"
filename = "hoseki-lustrousmix-pony-xl.Q4_K_M.gguf"
local_dir = "/var/home/EvokeStudio/.local/share/containers/storage/volumes/vox-conjurata_model_storage/_data"
target_filename = "hoseki_lustrousmix_pony_xl_q4.gguf"

print(f"Downloading {filename} from {repo_id}...")
try:
    path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=local_dir,
        token="hf_REDACTED"
    )
    
    # Rename to match convention
    target_path = os.path.join(local_dir, target_filename)
    if os.path.exists(target_path) and os.path.abspath(path) != os.path.abspath(target_path):
        os.remove(target_path)
    
    if os.path.abspath(path) != os.path.abspath(target_path):
        os.rename(path, target_path)
        print(f"Renamed model to {target_filename}")
        
    print(f"Download complete. Model saved to {target_path}")
except Exception as e:
    print(f"Error downloading model: {e}")
    exit(1)
