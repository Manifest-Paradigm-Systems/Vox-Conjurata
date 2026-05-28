import os
from huggingface_hub import hf_hub_download

base_repo = "EVA-UNIT-01/EVA-Qwen2.5-7B-v0.1"
local_dir = "/models/bartowski/EVA-Qwen2.5-7B-v0.1-GGUF"
files = ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "generation_config.json"]

print("Downloading config files...")
for file in files:
    try:
        hf_hub_download(
            repo_id=base_repo,
            filename=file,
            local_dir=local_dir,
            token=os.environ.get("HF_TOKEN")
        )
    except Exception as e:
        print(f"Skipped {file}: {e}")

print("Download complete.")
