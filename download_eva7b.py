import os
from huggingface_hub import hf_hub_download

repo_id = "bartowski/EVA-Qwen2.5-7B-v0.1-GGUF"
filename = "EVA-Qwen2.5-7B-v0.1-Q4_K_M.gguf"
local_dir = "/var/home/EvokeStudio/.local/share/containers/storage/volumes/vox-conjurata_model_storage/_data/bartowski/EVA-Qwen2.5-7B-v0.1-GGUF"

print(f"Downloading {filename} from {repo_id}...")
hf_hub_download(
    repo_id=repo_id,
    filename=filename,
    local_dir=local_dir,
    token=os.environ.get("HF_TOKEN")
)
print("Download complete.")
