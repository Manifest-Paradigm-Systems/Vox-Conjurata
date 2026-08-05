"""Unit tests for Workhorse UI v3: per-repo project registry, classification,
and bulk container actions."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as webui
from app import assign_project, PROJECT_REGISTRY, parse_mem_usage


# ---------------------------------------------------------------- registry

def test_registry_has_expected_projects():
    assert set(PROJECT_REGISTRY.keys()) == {
        "vox-conjurata", "AI-devteam", "vox-pdf-importer", "tiny11_CAC", "LifePacket",
    }


def test_registry_entries_have_repo_and_ai():
    for name, cfg in PROJECT_REGISTRY.items():
        assert cfg["repo"].startswith("/var/home/EvokeStudio/"), name
        assert cfg["ai"], f"project {name} missing AI description"
        assert cfg["containers"], f"project {name} missing containers"


# ---------------------------------------------------------------- assignment

def test_registry_drives_assignment():
    # AI-devteam containers come from the registry (no compose labels)
    for name in ("qa-box", "matrix-box", "conduit-live", "caddy-matrix",
                 "vox-voice", "vox-audio-core"):
        assert assign_project(name, {}) == "AI-devteam", name
    assert assign_project("adobe-reader", {}) == "vox-pdf-importer"
    assert assign_project("cacbox", {}) == "tiny11_CAC"
    assert assign_project("cac-setup", {}) == "tiny11_CAC"
    assert assign_project("ocr-box", {}) == "LifePacket"


def test_registry_wins_over_compose_label():
    # Explicit registry membership beats an inferred label
    labels = {"com.docker.compose.project": "vox-conjurata"}
    assert assign_project("cacbox", labels) == "tiny11_CAC"
    assert assign_project("qa-box", labels) == "AI-devteam"


def test_compose_project_label_is_source_of_truth():
    labels = {"com.docker.compose.project": "vox-conjurata"}
    for name in ("vox-vision-reader", "vox-conjurata-orchestrator",
                 "foundry-vtt", "vox-llm-core", "caddy", "cloudflared"):
        assert assign_project(name, labels) == "vox-conjurata", name


def test_other_for_unassigned():
    for name in ("rootsmagic", "jellyfin-https", "fedora-toolbox-44"):
        assert assign_project(name, {}) == "Other", name


def test_vox_name_convention_fallback():
    # No compose label, but vox-*/foundry naming still belongs to the stack
    assert assign_project("vox-bg-remover", {}) == "vox-conjurata"
    assert assign_project("foundry-vtt", {}) == "vox-conjurata"


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
        n = len(PROJECT_REGISTRY["AI-devteam"]["containers"])
        assert data["action"] == "start"
        assert data["ok"] == data["total"] == n
        assert all(r["ok"] for r in data["results"])
    assert calls == [["podman", "start", n] for n in PROJECT_REGISTRY["AI-devteam"]["containers"]]


def test_bulk_action_unknown_project():
    from fastapi.testclient import TestClient
    with TestClient(webui.app) as client:
        assert client.post("/api/project/nope/start").status_code == 404
        assert client.post("/api/project/vox-conjurata/frobnicate").status_code == 400


# ---------------------------------------------------------------- diagnostics

def test_parse_mem_usage():
    assert parse_mem_usage("9.866GB / 64.53GB") == 9.87
    assert parse_mem_usage("415.8MB / 64.53GB") == 0.42
    assert parse_mem_usage("1.2TB / 2TB") == 1200.0
    assert parse_mem_usage(None) is None
    assert parse_mem_usage("") is None
    assert parse_mem_usage("garbage") is None


def test_containers_merge_stats(monkeypatch):
    containers = [
        {"Id": "abc123deadbeef", "Names": ["vox-vision-reader"],
         "Status": "Up 1 hour", "Image": "img",
         "Labels": {"com.docker.compose.project": "vox-conjurata"}},
        {"Id": "def456cafebabe", "Names": ["cacbox"],
         "Status": "Exited (0)", "Image": "img", "Labels": {}},
    ]
    stats_json = json.dumps([
        {"name": "vox-vision-reader", "cpu_percent": "3.50%",
         "mem_usage": "4.8GB / 64.53GB", "mem_percent": "7.44%", "pids": "37"},
    ])

    def fake_run(cmd, capture_output=None, text=None, timeout=None):
        if cmd[:2] == ["podman", "ps"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(containers), stderr="")
        if cmd[:2] == ["podman", "stats"]:
            return SimpleNamespace(returncode=0, stdout=stats_json, stderr="")
        if cmd[0] == "podman" and cmd[1] == "inspect":
            inspect_json = json.dumps([
                {"Name": "vox-vision-reader",
                 "HostConfig": {"NanoCpus": 4000000000, "Memory": 4294967296,
                                "RestartPolicy": {"Name": "always"}, "PidsLimit": 1024},
                 "State": {"Pid": 99999}},
                {"Name": "cacbox",
                 "HostConfig": {"NanoCpus": 0, "Memory": 0,
                                "RestartPolicy": {"Name": "no"}, "PidsLimit": 0},
                 "State": {"Pid": 0}},
            ])
            return SimpleNamespace(returncode=0, stdout=inspect_json, stderr="")
        raise AssertionError(f"unexpected podman call: {cmd}")

    monkeypatch.setattr(webui.subprocess, "run", fake_run)
    from fastapi.testclient import TestClient
    with TestClient(webui.app) as client:
        data = client.get("/api/containers").json()
    by_name = {c["name"]: c for c in data}
    up = by_name["vox-vision-reader"]
    assert up["id"] == "abc123deadbe"  # Id key fixed (12-char truncation), not N/A
    assert up["mem_usage"] == "4.8GB / 64.53GB"
    assert up["mem_gb"] == 4.8
    assert up["mem_percent"] == "7.44%"
    assert up["cpu_percent"] == "3.50%"
    assert up["pids"] == "37"
    # stopped container gets no stats
    stopped = by_name["cacbox"]
    assert stopped["mem_usage"] is None
    assert stopped["cpu_percent"] is None
    # limits parsed from inspect (pid 99999 not readable in tests → no peak)
    assert up["settings"] == {"cpu_cores": 4.0, "mem_limit_gb": 4.0,
                              "restart_policy": "always", "pids_limit": 1024}
    assert up["peak_mem_gb"] is None
    assert stopped["settings"]["cpu_cores"] == 0.0


def test_settings_endpoint_builds_flags(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output=None, text=None, timeout=None):
        calls.append(list(cmd))
        if cmd[0] == "podman" and cmd[1] == "update":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == "podman" and cmd[1] == "inspect":
            return SimpleNamespace(returncode=0, stdout=json.dumps([
                {"Name": "cacbox",
                 "HostConfig": {"NanoCpus": 2000000000, "Memory": 4294967296,
                                "RestartPolicy": {"Name": "on-failure"}, "PidsLimit": 512},
                 "State": {"Pid": 0}},
            ]), stderr="")
        raise AssertionError(f"unexpected: {cmd}")

    monkeypatch.setattr(webui.subprocess, "run", fake_run)
    from fastapi.testclient import TestClient
    with TestClient(webui.app) as client:
        resp = client.post("/api/container/cacbox/settings", json={
            "cpu_cores": 2, "mem_limit_gb": 4, "restart_policy": "on-failure", "pids_limit": 512,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"]["cpu_cores"] == 2.0
        assert data["settings"]["mem_limit_gb"] == 4.0
        assert data["settings"]["restart_policy"] == "on-failure"
        assert data["settings"]["pids_limit"] == 512
    update_call = [c for c in calls if c[:2] == ["podman", "update"]][0]
    assert update_call == ["podman", "update", "--cpus", "2", "--memory", "4g",
                           "--restart", "on-failure", "--pids-limit", "512", "cacbox"]


def test_settings_validation():
    from fastapi.testclient import TestClient
    with TestClient(webui.app) as client:
        assert client.post("/api/container/cacbox/settings", json={"cpu_cores": 999}).status_code == 400
        assert client.post("/api/container/cacbox/settings", json={"mem_limit_gb": -3}).status_code == 400
        assert client.post("/api/container/cacbox/settings", json={"restart_policy": "bogus"}).status_code == 400
        assert client.post("/api/container/cacbox/settings", json={"pids_limit": -5}).status_code == 400
        assert client.post("/api/container/cacbox/settings", json={}).status_code == 400


def test_bulk_stop_returns_failures(monkeypatch):
    target = PROJECT_REGISTRY["LifePacket"]["containers"][0]

    def fake_run(cmd, capture_output=None, text=None):
        if cmd[1] == "stop" and cmd[2] == target:
            return SimpleNamespace(returncode=1, stdout="", stderr="no such container")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(webui.subprocess, "run", fake_run)
    from fastapi.testclient import TestClient
    with TestClient(webui.app) as client:
        resp = client.post("/api/project/LifePacket/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == len(PROJECT_REGISTRY["LifePacket"]["containers"])
        assert data["ok"] == data["total"] - 1
        failed = [r for r in data["results"] if not r["ok"]]
        assert failed == [{"name": target, "ok": False, "detail": "no such container"}]
