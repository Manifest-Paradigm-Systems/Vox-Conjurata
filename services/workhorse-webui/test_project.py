"""Unit tests for Workhorse UI v3: per-repo project registry, classification,
and bulk container actions."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as webui
from app import assign_project, PROJECT_REGISTRY


# ---------------------------------------------------------------- registry

def test_registry_has_expected_projects():
    assert set(PROJECT_REGISTRY.keys()) == {"vox-conjurata", "LifePacket", "AI-devteam"}


def test_registry_entries_have_repo_and_ai():
    for name, cfg in PROJECT_REGISTRY.items():
        assert cfg["repo"].startswith("/var/home/EvokeStudio/"), name
        assert cfg["ai"], f"project {name} missing AI description"
        assert cfg["containers"], f"project {name} missing containers"


# ---------------------------------------------------------------- assignment

def test_registry_drives_assignment():
    # AI-devteam containers come from the registry (no compose labels)
    for name in ("qa-box", "matrix-box", "conduit-live", "caddy-matrix"):
        assert assign_project(name, {}) == "AI-devteam", name


def test_registry_wins_over_compose_label():
    # Explicit registry membership beats an inferred label
    labels = {"com.docker.compose.project": "vox-conjurata"}
    assert assign_project("cacbox", labels) == "LifePacket"
    assert assign_project("qa-box", labels) == "AI-devteam"


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
    assert assign_project("vox-bg-remover", {}) == "vox-conjurata"
    assert assign_project("foundry-vtt", {}) == "vox-conjurata"


def test_other_for_unassigned():
    for name in ("rootsmagic", "jellyfin-https", "fedora-toolbox-44",
                 "adobe-reader"):
        assert assign_project(name, {}) == "Other", name


# ---------------------------------------------------------------- API

def _fake_podman_ps(containers):
    def fake_run(cmd, capture_output=None, text=None):
        if cmd[:2] == ["podman", "ps"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(containers), stderr="")
        raise AssertionError(f"unexpected podman call: {cmd}")
    return fake_run


def test_projects_endpoint_live_state(monkeypatch):
    containers = [
        {"Names": ["qa-box"], "Status": "Up 3 hours"},
        {"Names": ["conduit-live"], "Status": "Exited (0)"},
    ]
    monkeypatch.setattr(webui.subprocess, "run", _fake_podman_ps(containers))
    from fastapi.testclient import TestClient
    with TestClient(webui.app) as client:
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        projects = {p["name"]: p for p in resp.json()["projects"]}
        assert projects["AI-devteam"]["all_up"] is False
        states = {c["name"]: c["status"] for c in projects["AI-devteam"]["containers"]}
        assert states["qa-box"] == "Up 3 hours"
        assert states["conduit-live"] == "Exited (0)"
        assert projects["vox-conjurata"]["ai"]


def test_bulk_start_endpoint(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output=None, text=None):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(webui.subprocess, "run", fake_run)
    from fastapi.testclient import TestClient
    with TestClient(webui.app) as client:
        resp = client.post("/api/project/AI-devteam/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "start"
        assert data["ok"] == data["total"] == 4
        assert all(r["ok"] for r in data["results"])
    assert calls == [["podman", "start", n] for n in PROJECT_REGISTRY["AI-devteam"]["containers"]]


def test_bulk_action_unknown_project():
    from fastapi.testclient import TestClient
    with TestClient(webui.app) as client:
        assert client.post("/api/project/nope/start").status_code == 404
        assert client.post("/api/project/vox-conjurata/frobnicate").status_code == 400


def test_bulk_stop_returns_failures(monkeypatch):
    def fake_run(cmd, capture_output=None, text=None):
        if cmd[1] == "stop" and cmd[2] == "cacbox":
            return SimpleNamespace(returncode=1, stdout="", stderr="no such container")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(webui.subprocess, "run", fake_run)
    from fastapi.testclient import TestClient
    with TestClient(webui.app) as client:
        resp = client.post("/api/project/LifePacket/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] == 2
        assert data["total"] == 3
        failed = [r for r in data["results"] if not r["ok"]]
        assert failed == [{"name": "cacbox", "ok": False, "detail": "no such container"}]
