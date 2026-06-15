import os
from huggingface_hub import hf_hub_download

repo_id = "city96/Juggernaut-XL-Lightning-GGUF"
filename = "Juggernaut-XL-Lightning-Q4_0.gguf"
local_dir = "/var/home/EvokeStudio/.local/share/containers/storage/volumes/vox-conjurata_model_storage/_data"
target_filename = "juggernaut_xl_lightning_q4_0.gguf"

print(f"Downloading {filename} from {repo_id}...")
try:
    # city96 repo often has the file in a subfolder or root, let's try downloading without renaming first to see path
    path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        token="hf_REDACTED"
    )
    
    # Move to the volume directory
    target_path = os.path.join(local_dir, target_filename)
    if os.path.exists(target_path):
        os.remove(target_path)
    
    import shutil
    shutil.copy(path, target_path)
    print(f"Download complete. Model saved to {target_path}")
except Exception as e:
    print(f"Error downloading model: {e}")
    exit(1)
