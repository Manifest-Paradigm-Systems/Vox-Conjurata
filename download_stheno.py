import os
from huggingface_hub import hf_hub_download

repo_id = "Lewdiculous/L3-8B-Stheno-v3.2-GGUF-IQ-Imatrix"
filename = "L3-8B-Stheno-v3.2-Q4_K_M-imat.gguf"
local_dir = "/models/Lewdiculous/L3-8B-Stheno-v3.2-GGUF-IQ-Imatrix"

print(f"Downloading {filename} from {repo_id}...")
hf_hub_download(
    repo_id=repo_id,
    filename=filename,
    local_dir=local_dir,
    token=os.environ.get("HF_TOKEN")
)
print("Download complete.")
