#!/bin/bash
# Install cerebro LLM units as system services (survive reboots without login).
# Run:  ./install.sh   (will prompt for sudo password)
set -e
echo "==> creating vox-net + pulling image into ROOT podman storage (one-time ~23GB)..."
sudo podman network create vox-net 2>/dev/null || true
sudo podman pull ghcr.io/ggml-org/llama.cpp:server-rocm
echo "==> installing units..."
sudo cp /var/home/admin/cerebro-units/container-*.service /etc/systemd/system/
sudo systemctl daemon-reload
echo "==> enabling + starting (replaces the ad-hoc containers cleanly)..."
sudo systemctl enable --now container-director container-coder container-actor container-ears
echo "==> DONE. Verify: sudo systemctl status container-director (and coder/actor/ears)"
