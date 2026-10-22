"""Static checks for the container and Compose definitions (PLAN_P1 1.4/1.5)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_the_image_is_pinned_and_runs_as_a_non_root_user():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python:3.12-slim-bookworm@sha256:" in dockerfile
    assert "ARG GIT_VERSION=1:2.39.5-0+deb12u3" in dockerfile
    assert "ARG OPENSSH_VERSION=1:9.2p1-2+deb12u10" in dockerfile
    assert 'git="${GIT_VERSION}" openssh-client="${OPENSSH_VERSION}"' in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/health/ready" in dockerfile
    assert "docker.sock" not in dockerfile
    assert "pip install --no-cache-dir -r /tmp/requirements.lock" in dockerfile


def test_the_runtime_dependencies_are_locked_including_cryptography():
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")

    assert "cryptography==" in lock
    assert "fastapi==" in lock
    assert "uvicorn==" in lock


def test_compose_publishes_loopback_only_and_writes_only_to_mounted_paths():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    caliper = compose["services"]["caliper"]

    assert caliper["ports"] == ["127.0.0.1:8080:8080"]
    assert caliper["read_only"] is True
    assert caliper["volumes"] == ["caliper-data:/data"]
    assert caliper["mem_limit"] == "2g"
    assert caliper["secrets"] == ["caliper_master_key"]
    assert caliper["environment"]["CALIPER_MASTER_KEY_FILE"] == "/run/secrets/caliper_master_key"
    assert caliper["security_opt"] == ["no-new-privileges:true"]
    assert caliper["cap_drop"] == ["ALL"]
    tmpfs = " ".join(caliper["tmpfs"])
    assert "/tmp:mode=1777" in tmpfs
    assert "/run/caliper:mode=0700,uid=10001,gid=10001" in tmpfs
    # The host Docker socket is never mounted.
    assert all("docker.sock" not in volume for volume in caliper["volumes"])
    secret_file = compose["secrets"]["caliper_master_key"]["file"]
    assert secret_file == "${CALIPER_MASTER_KEY_HOST_FILE:-./secrets/master.key}"
    assert secret_file.endswith("./secrets/master.key}")


def test_ollama_is_opt_in_and_on_the_internal_network_only():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    ollama = compose["services"]["ollama"]

    assert ollama["profiles"] == ["ollama"]
    assert "ports" not in ollama
    assert ollama["expose"] == ["11434"]
    assert "ollama-models:/root/.ollama" in ollama["volumes"]


def test_container_context_never_includes_real_secrets_or_state():
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()

    for entry in ("secrets", "data", "projects", ".caliper-state", ".venv"):
        assert entry in dockerignore
