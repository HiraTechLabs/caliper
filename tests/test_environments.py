import base64
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.registry import ProjectRegistry
from caliper.remote import RemoteError, _fingerprint_of, pin_host_key
from caliper.remote import test_environment as check_environment
from caliper.state import RequestStore

HOST_KEY_BLOB = base64.b64encode(bytes(range(32))).decode("ascii")
OTHER_HOST_KEY_BLOB = base64.b64encode(bytes(range(32, 64))).decode("ascii")
HOST_KEY_LINE = f"dev.example.com ssh-ed25519 {HOST_KEY_BLOB}"


def _key_generator(path: Path, comment: str) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("PRIVATE KEY", encoding="utf-8")
    public = path.with_name(path.name + ".pub")
    public.write_text(f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI{comment}", encoding="utf-8")
    return public.read_text(encoding="utf-8")


class FakeRunner:
    """Stands in for the ssh/ssh-keyscan calls and records what was run."""

    def __init__(self, key_line: str = HOST_KEY_LINE, failures: dict | None = None):
        self.key_line = key_line
        self.failures = failures or {}
        self.calls: list[list[str]] = []

    def __call__(self, command, cwd=None, env=None):
        self.calls.append(list(command))
        joined = " ".join(command)
        for needle, (code, output) in self.failures.items():
            if needle in joined:
                return code, output
        if "ssh-keyscan" in command[0]:
            return 0, self.key_line
        return 0, ""

    def commands(self) -> list[str]:
        return [" ".join(call) for call in self.calls]


@pytest.fixture()
def registry(tmp_path):
    registry = ProjectRegistry(
        tmp_path / "projects", secrets_dir=tmp_path / "secrets", key_generator=_key_generator
    )
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    return registry


def _client(registry, tmp_path):
    requests = RequestStore(tmp_path / "state.sqlite3")
    return TestClient(create_app(registry=registry, requests=requests))


ENV_FIELDS = {
    "host": "dev.example.com",
    "user": "deploy",
    "deploy_path": "/srv/app",
    "compose_file": "docker-compose.yml",
    "health_url": "https://dev.example.com/health",
}


def test_environment_key_is_generated_in_the_secrets_dir_and_never_in_the_manifest(registry, tmp_path):
    client = _client(registry, tmp_path)
    response = client.post("/api/projects/shop-api/environments/dev/key")
    assert response.status_code == 200
    body = response.json()
    key_path = Path(body["key_path"])
    assert key_path.is_file()
    assert str(tmp_path / "secrets") in str(key_path)
    assert "PRIVATE KEY" not in body["public_key"]
    assert body["public_key"].startswith("ssh-ed25519")
    # The private key never reaches the manifest.
    manifest = registry.manifest("shop-api")
    assert "PRIVATE KEY" not in str(manifest)
    assert "ssh-ed25519" not in str(manifest["environments"]["dev"])


@pytest.mark.skipif(os.name == "nt", reason="Windows does not implement POSIX file modes")
def test_environment_private_key_is_owner_only(registry, tmp_path):
    client = _client(registry, tmp_path)
    body = client.post("/api/projects/shop-api/environments/dev/key").json()
    mode = stat.S_IMODE(Path(body["key_path"]).stat().st_mode)
    assert mode == 0o600


def test_api_returns_the_public_key_and_the_exact_ssh_copy_id_command(registry, tmp_path):
    client = _client(registry, tmp_path)
    client.put("/api/projects/shop-api/environments/dev", json=ENV_FIELDS)
    body = client.get("/api/projects/shop-api/environments").json()
    dev = [e for e in body["environments"] if e["env"] == "dev"][0]
    assert dev["public_key"].startswith("ssh-ed25519")
    assert dev["ssh_copy_id_command"] == f"ssh-copy-id -i {dev['key_path']} deploy@dev.example.com"
    assert dev["key_path"] in dev["ssh_copy_id_command"]
    assert dev["host_key_fingerprint"] is None
    assert dev["verified"] is False


def test_environment_stores_no_password(registry, tmp_path):
    client = _client(registry, tmp_path)
    client.put("/api/projects/shop-api/environments/dev", json={**ENV_FIELDS, "password": "secret"})
    entry = registry.manifest("shop-api")["environments"]["dev"]
    assert "password" not in entry
    assert "secret" not in str(entry)


def test_saving_an_environment_requires_the_fields(registry, tmp_path):
    client = _client(registry, tmp_path)
    response = client.put("/api/projects/shop-api/environments/dev", json={"host": "dev.example.com"})
    assert response.status_code == 422
    assert "user" in response.json()["error"]


def test_test_connection_runs_batchmode_docker_compose_and_the_deploy_path(registry, tmp_path, monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr("caliper.remote._run", runner)
    key_path = tmp_path / "secrets" / "projects" / "shop-api" / "env_dev"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text("PRIVATE KEY", encoding="utf-8")
    known_hosts = tmp_path / "secrets" / "projects" / "shop-api" / "known_hosts_dev"

    result = check_environment(
        "dev.example.com", "deploy", key_path,
        deploy_path="/srv/app", compose_file="docker-compose.yml", known_hosts_path=known_hosts,
    )

    assert result["host_key_fingerprint"].startswith("SHA256:")
    commands = runner.commands()
    ssh_calls = [c for c in commands if c.startswith("ssh ")]
    assert ssh_calls, "no ssh call was made"
    for call in ssh_calls:
        assert "-o BatchMode=yes" in call
        assert "-o StrictHostKeyChecking=yes" in call
        assert f"UserKnownHostsFile={known_hosts}" in call
    assert any(c.endswith(" true") for c in ssh_calls)
    assert any("docker compose version" in c for c in ssh_calls)
    assert any("test -d /srv/app && test -w /srv/app" in c for c in ssh_calls)


def test_test_connection_pins_the_host_key_and_shows_the_fingerprint(registry, tmp_path, monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr("caliper.remote._run", runner)
    client = _client(registry, tmp_path)
    client.put("/api/projects/shop-api/environments/dev", json=ENV_FIELDS)

    response = client.post("/api/projects/shop-api/environments/dev/test")
    assert response.status_code == 200
    body = response.json()
    assert body["environment"]["host_key_fingerprint"].startswith("SHA256:")
    assert body["environment"]["verified"] is True
    known_hosts = Path(body["environment"]["known_hosts_path"])
    assert known_hosts.is_file()
    assert HOST_KEY_BLOB in known_hosts.read_text(encoding="utf-8")


def test_a_changed_host_key_fails_loudly(registry, tmp_path, monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr("caliper.remote._run", runner)
    client = _client(registry, tmp_path)
    client.put("/api/projects/shop-api/environments/dev", json=ENV_FIELDS)
    first = client.post("/api/projects/shop-api/environments/dev/test")
    assert first.status_code == 200
    pinned = first.json()["environment"]["host_key_fingerprint"]

    runner.key_line = f"dev.example.com ssh-ed25519 {OTHER_HOST_KEY_BLOB}"
    second = client.post("/api/projects/shop-api/environments/dev/test")
    assert second.status_code == 422
    error = second.json()["error"]
    assert "host key" in error.lower()
    assert "changed" in error.lower()
    assert pinned in error
    assert second.json()["environment"]["verified"] is False


def test_pin_host_key_raises_when_the_key_changes(tmp_path, monkeypatch):
    known_hosts = tmp_path / "known_hosts"
    monkeypatch.setattr(
        "caliper.remote.scan_host_key",
        lambda host, port=22: (_fingerprint_of(HOST_KEY_LINE), HOST_KEY_LINE),
    )
    pinned = pin_host_key("dev.example.com", known_hosts)
    assert pinned["fingerprint"].startswith("SHA256:")
    assert known_hosts.is_file()

    other_line = f"dev.example.com ssh-ed25519 {OTHER_HOST_KEY_BLOB}"
    monkeypatch.setattr(
        "caliper.remote.scan_host_key",
        lambda host, port=22: (_fingerprint_of(other_line), other_line),
    )
    with pytest.raises(RemoteError, match="changed"):
        pin_host_key("dev.example.com", known_hosts)


def test_permission_denied_is_reported_with_the_ssh_copy_id_command(tmp_path, monkeypatch):
    runner = FakeRunner(failures={" true": (255, "deploy@dev.example.com: Permission denied (publickey).")})
    monkeypatch.setattr("caliper.remote._run", runner)
    key_path = tmp_path / "env_dev"
    key_path.write_text("PRIVATE KEY", encoding="utf-8")

    with pytest.raises(RemoteError) as error:
        check_environment("dev.example.com", "deploy", key_path, deploy_path="/srv/app",
                         known_hosts_path=tmp_path / "known_hosts")
    message = str(error.value)
    assert "not authorized" in message
    assert f"ssh-copy-id -i {key_path} deploy@dev.example.com" in message


def test_missing_docker_compose_is_reported(tmp_path, monkeypatch):
    runner = FakeRunner(failures={"docker compose version": (1, "docker: command not found")})
    monkeypatch.setattr("caliper.remote._run", runner)
    key_path = tmp_path / "env_dev"
    key_path.write_text("PRIVATE KEY", encoding="utf-8")

    with pytest.raises(RemoteError) as error:
        check_environment("dev.example.com", "deploy", key_path, deploy_path="/srv/app",
                         known_hosts_path=tmp_path / "known_hosts")
    assert "docker compose version" in str(error.value)


def test_a_missing_deploy_path_is_reported_with_the_fix_command(tmp_path, monkeypatch):
    runner = FakeRunner(failures={"test -d": (1, "")})
    monkeypatch.setattr("caliper.remote._run", runner)
    key_path = tmp_path / "env_dev"
    key_path.write_text("PRIVATE KEY", encoding="utf-8")

    with pytest.raises(RemoteError) as error:
        check_environment("dev.example.com", "deploy", key_path, deploy_path="/srv/app",
                         known_hosts_path=tmp_path / "known_hosts")
    message = str(error.value)
    assert "/srv/app" in message
    assert "ssh deploy@dev.example.com 'mkdir -p /srv/app'" in message


def test_a_host_that_does_not_resolve_is_reported_in_plain_language(tmp_path, monkeypatch):
    runner = FakeRunner(failures={"ssh-keyscan": (1, "getaddrinfo dev.example.com: No such host is known")})
    monkeypatch.setattr("caliper.remote._run", runner)
    key_path = tmp_path / "env_dev"
    key_path.write_text("PRIVATE KEY", encoding="utf-8")

    with pytest.raises(RemoteError) as error:
        check_environment("dev.example.com", "deploy", key_path, deploy_path="/srv/app",
                          known_hosts_path=tmp_path / "known_hosts")
    message = str(error.value)
    assert "does not resolve" in message
    assert "No such host" not in message


def test_a_failed_test_is_reported_by_the_api(registry, tmp_path, monkeypatch):
    runner = FakeRunner(failures={" true": (255, "connect to host dev.example.com port 22: Connection refused")})
    monkeypatch.setattr("caliper.remote._run", runner)
    client = _client(registry, tmp_path)
    client.put("/api/projects/shop-api/environments/dev", json=ENV_FIELDS)

    response = client.post("/api/projects/shop-api/environments/dev/test")
    assert response.status_code == 422
    assert "nothing is listening for SSH" in response.json()["error"]
    assert registry.manifest("shop-api")["environments"]["dev"]["verified"] is False
