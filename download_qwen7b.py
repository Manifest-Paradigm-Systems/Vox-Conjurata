import os
from huggingface_hub import hf_hub_download

repo_id = "bartowski/Qwen2.5-7B-Instruct-GGUF"
filename = "Qwen2.5-7B-Instruct-Q4_K_M.gguf"
local_dir = "/models/bartowski/Qwen2.5-7B-Instruct-GGUF"

print(f"Downloading {filename} from {repo_id}...")
hf_hub_download(
    repo_id=repo_id,
    filename=filename,
    local_dir=local_dir,
    token=os.environ.get("HF_TOKEN")
)
print("Download complete.")
