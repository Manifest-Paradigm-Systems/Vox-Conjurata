"""Unit tests for Workhorse UI v2 project classification (project tabs)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import assign_project


def test_compose_project_label_is_source_of_truth():
    labels = {"com.docker.compose.project": "vox-conjurata"}
    for name in ("vox-vision-reader", "vox-conjurata-orchestrator",
                 "foundry-vtt", "vox-llm-core", "caddy", "cloudflared"):
        assert assign_project(name, labels) == "vox-conjurata", name


def test_lifepacket_table():
    for name in ("cacbox", "cac-setup", "ocr-box"):
        assert assign_project(name, {}) == "LifePacket", name


def test_vox_name_convention_fallback():
    # No compose label, but vox-*/foundry naming still belongs to the stack
    assert assign_project("vox-vision-gen", {}) == "vox-conjurata"
    assert assign_project("vox-audio-core", {}) == "vox-conjurata"
    assert assign_project("foundry-vtt", {}) == "vox-conjurata"


def test_other_for_unassigned():
    for name in ("rootsmagic", "jellyfin-https", "fedora-toolbox-44",
                 "adobe-reader", "qa-box", "matrix-box", "conduit-live",
                 "caddy-matrix"):
        assert assign_project(name, {}) == "Other", name


def test_lifepacket_not_captured_by_vox_convention():
    # cac* names must never land in vox-conjurata via the vox- prefix rule
    assert assign_project("cacbox", {}) == "LifePacket"
    assert assign_project("cac-setup", {}) == "LifePacket"
