"""End-to-end acceptance checks for the built container image (PLAN_P1 1.5).

These tests need a Docker daemon and a built image, so they are skipped unless
``CALIPER_CONTAINER_IMAGE`` names one::

    docker build --target runtime -t caliper:phase1 .
    CALIPER_CONTAINER_IMAGE=caliper:phase1 pytest tests/test_container_integration.py -q

They run the image exactly as Compose does: read-only root filesystem, non-root
UID 10001, writable ``/data`` volume and tmpfs runtime directory, master key
mounted as a secret file, no Docker socket. The checks prove the container is
usable, not merely that it starts.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest

IMAGE = os.environ.get("CALIPER_CONTAINER_IMAGE", "").strip()

pytestmark = pytest.mark.skipif(
    not IMAGE,
    reason="set CALIPER_CONTAINER_IMAGE to a built image to run container acceptance tests",
)


def _docker(*args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _inspect(name: str) -> dict:
    return json.loads(_docker("inspect", name).stdout)[0]


def _docker_bytes(*args: str, timeout: int = 120) -> bytes:
    """Run Docker and return raw bytes (for binary files such as SQLite)."""
    return subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        timeout=timeout,
    ).stdout


def _published_port(name: str) -> str:
    """Host port for container port 8080; Docker may reassign it on restart."""
    ports = _inspect(name)["NetworkSettings"]["Ports"]["8080/tcp"]
    return ports[0]["HostPort"]


def _http(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post(url: str, form: dict | None = None, timeout: float = 20.0) -> tuple[int, bytes]:
    data = urllib.parse.urlencode(form).encode("utf-8") if form is not None else b""
    request = urllib.request.Request(url, data=data, method="POST")
    if form is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _get_json(url: str) -> dict:
    status, body = _http(url)
    assert status == 200, (status, body[:500])
    return json.loads(body)


def _wait_ready(base_url: str, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    last: tuple[int, bytes] | None = None
    while time.monotonic() < deadline:
        try:
            status, body = _http(f"{base_url}/health/ready", timeout=3)
            last = (status, body)
            if status == 200:
                return json.loads(body)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.5)
    raise AssertionError(f"container never became ready; last response: {last}")


class RunningContainer:
    def __init__(self, name: str, volume: str, base_url: str, key: str, public_key: str):
        self.name = name
        self.volume = volume
        self.base_url = base_url
        self.key = key
        self.public_key = public_key

    def restart(self) -> None:
        _docker("restart", "-t", "20", self.name, timeout=180)
        # An ephemeral host port (``-p 127.0.0.1::8080``) is reassigned by
        # Docker when the container restarts, so read it again.
        self.base_url = f"http://127.0.0.1:{_published_port(self.name)}"
        _wait_ready(self.base_url)

    def logs(self) -> str:
        return _docker("logs", self.name, check=False).stdout

    def exec(self, *command: str) -> subprocess.CompletedProcess:
        return _docker("exec", self.name, *command, check=False)


@pytest.fixture(scope="module")
def container(tmp_path_factory) -> RunningContainer:
    if shutil.which("docker") is None:
        pytest.skip("the docker CLI is not installed")
    try:
        _docker("info", timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("no Docker daemon is reachable")

    workdir = tmp_path_factory.mktemp("caliper-container")
    secret_file = workdir / "master.key"
    # 32 random bytes, base64-encoded exactly as `python -m caliper init-secrets` does.
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    secret_file.write_text(key + "\n", encoding="utf-8")
    os.chmod(secret_file, 0o644)  # readable by UID 10001 inside the container

    name = f"caliper-it-{uuid.uuid4().hex[:8]}"
    volume = f"caliper-it-data-{uuid.uuid4().hex[:8]}"
    _docker("volume", "create", volume)

    _docker(
        "run", "-d", "--name", name,
        "-e", "CALIPER_HOST=0.0.0.0",
        "-e", "CALIPER_PORT=8080",
        "-e", "CALIPER_LOG_LEVEL=INFO",
        "-e", "CALIPER_MASTER_KEY_FILE=/run/secrets/caliper_master_key",
        "-v", f"{secret_file}:/run/secrets/caliper_master_key:ro",
        "-v", f"{volume}:/data",
        "--read-only",
        "--tmpfs", "/tmp:mode=1777",
        "--tmpfs", "/run/caliper:mode=0700,uid=10001,gid=10001",
        "--security-opt", "no-new-privileges:true",
        "--cap-drop", "ALL",
        "-p", "127.0.0.1::8080",
        IMAGE,
    )
    try:
        port = _published_port(name)
        base_url = f"http://127.0.0.1:{port}"
        _wait_ready(base_url)

        created_status, created_body = _post(
            f"{base_url}/projects/new",
            {"name": "Container Project", "repo_url": "git@github.com:example/container-project.git"},
        )
        assert created_status == 200, created_body[:500]
        key_status, key_body = _post(
            f"{base_url}/api/projects/container-project/repository/key"
        )
        assert key_status == 200, key_body[:500]
        public_key = json.loads(key_body)["public_key"]
        assert public_key.startswith("ssh-ed25519 ")

        yield RunningContainer(name, volume, base_url, key, public_key)
    finally:
        _docker("rm", "-f", name, check=False, timeout=60)
        _docker("volume", "rm", "-f", volume, check=False, timeout=60)


def test_the_container_is_ready_read_only_non_root_and_secret_free(container: RunningContainer):
    live_status, live_body = _http(f"{container.base_url}/health/live")
    ready = _get_json(f"{container.base_url}/health/ready")

    assert live_status == 200
    assert json.loads(live_body)["check"] == "live"
    assert ready["status"] == "ready"
    assert ready["checks"]["database"]["ok"] is True
    assert ready["checks"]["data_dir"]["ok"] is True
    assert ready["checks"]["secret_store"]["ok"] is True

    inspected = _inspect(container.name)
    assert inspected["Config"]["User"] == "10001:10001"
    assert inspected["HostConfig"]["ReadonlyRootfs"] is True
    assert inspected["HostConfig"]["CapDrop"] == ["ALL"]
    assert "no-new-privileges:true" in (inspected["HostConfig"].get("SecurityOpt") or [])
    assert all("docker.sock" not in mount.get("Source", "") for mount in inspected["Mounts"])

    uid = container.exec("id", "-u")
    assert uid.returncode == 0, uid.stderr
    assert uid.stdout.strip() == "10001"

    # A generated deploy key must not leave plaintext in the runtime tmpfs.
    runtime_listing = container.exec(
        "python", "-c", "import os; print('\\n'.join(os.listdir('/run/caliper')))"
    )
    assert runtime_listing.returncode == 0, runtime_listing.stderr
    assert runtime_listing.stdout.strip() == "", runtime_listing.stdout

    logs = container.logs()
    assert container.key not in logs
    assert "BEGIN OPENSSH PRIVATE KEY" not in logs
    json_lines = [line for line in logs.splitlines() if line.startswith("{")]
    assert json_lines, logs[:1000]
    for line in json_lines:
        payload = json.loads(line)
        assert {"ts", "level", "logger", "message"} <= set(payload)


def test_projects_and_encrypted_keys_survive_a_restart_and_sigterm(container: RunningContainer):
    container.restart()

    ready = _get_json(f"{container.base_url}/health/ready")
    assert ready["status"] == "ready"

    after = _get_json(f"{container.base_url}/api/projects/container-project/repository")
    assert after["public_key"] == container.public_key
    assert after["url"] == "git@github.com:example/container-project.git"

    # The project row, manifest and encrypted store all live under /data.
    store = container.exec(
        "python", "-c",
        "import sqlite3; c=sqlite3.connect('/data/secrets/store.sqlite3'); "
        "print(c.execute('select count(*) from secrets').fetchone()[0])",
    )
    assert store.returncode == 0, store.stderr
    assert int(store.stdout.strip()) >= 2

    raw = _docker_bytes("exec", container.name, "cat", "/data/secrets/store.sqlite3")
    assert b"BEGIN OPENSSH PRIVATE KEY" not in raw
    assert container.key.encode("ascii") not in raw

    stopped = _docker("stop", "-t", "20", container.name, timeout=60)
    assert stopped.returncode == 0, stopped.stderr
    final = _inspect(container.name)
    assert final["State"]["ExitCode"] == 0
    assert final["State"]["OOMKilled"] is False
