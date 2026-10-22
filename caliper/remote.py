import base64
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

HOST_KEY_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "rsa-sha2-512", "rsa-sha2-256", "ssh-rsa")


class RemoteError(Exception):
    pass


def _run(command: list[str], cwd: Path | None = None, env: dict | None = None, timeout: float = 45,
         input_text: str | None = None) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired:
        return 124, "The command timed out."
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return completed.returncode, output[-1200:]


def generate_ed25519_key(path: Path, comment: str) -> str:
    path = Path(path)
    public_path = path.with_name(path.name + ".pub")
    if path.is_file() and public_path.is_file():
        return public_path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        raise RemoteError("ssh-keygen is not installed; cannot create the deploy key.")
    safe_comment = re.sub(r"[^A-Za-z0-9_.-]+", "-", comment)
    code, output = _run([keygen, "-t", "ed25519", "-N", "", "-C", safe_comment, "-f", str(path)])
    if code != 0 or not public_path.is_file():
        raise RemoteError(f"ssh-keygen failed: {output or 'no output'}")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return public_path.read_text(encoding="utf-8").strip()


def ssh_url(repo_url: str) -> str:
    url = (repo_url or "").strip()
    if not url:
        raise RemoteError("Repository URL is empty.")
    if url.startswith("git@") or url.startswith("ssh://"):
        return url
    if url.startswith("file://"):
        return url
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return url
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.path.strip("/"):
        raise RemoteError("Repository URL must be an HTTPS or SSH Git URL.")
    path = parsed.path.lstrip("/")
    if not path.endswith(".git"):
        path += ".git"
    return f"git@{parsed.netloc}:{path}"


def repo_keys_url(repo_url: str) -> str:
    url = (repo_url or "").strip()
    if url.startswith("git@") and ":" in url:
        netloc, path = url[4:].split(":", 1)
    else:
        parsed = urlparse(url)
        netloc = parsed.netloc.split("@")[-1]
        path = parsed.path
    if netloc.lower() != "github.com":
        return ""
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path:
        return ""
    return f"https://github.com/{path}/settings/keys"


def _git_env(key_path: Path) -> dict:
    env = os.environ.copy()
    quoted = str(Path(key_path))
    env["GIT_SSH_COMMAND"] = (
        f'ssh -i "{quoted}" -o IdentitiesOnly=yes -o BatchMode=yes '
        "-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
    )
    return env


def test_repository(repo_url: str, key_path: Path) -> str:
    if not Path(key_path).is_file():
        raise RemoteError("Generate the repository deploy key first.")
    url = ssh_url(repo_url)
    env = _git_env(key_path)
    code, output = _run(["git", "ls-remote", url], env=env)
    if code != 0:
        raise RemoteError(f"Read check failed: {output or 'git ls-remote failed'}")
    with tempfile.TemporaryDirectory(prefix="caliper-access-") as temp:
        work = Path(temp)
        for command in (
            ["git", "init", "-q"],
            ["git", "-c", "user.name=Caliper", "-c", "user.email=caliper@localhost", "commit", "--allow-empty", "-qm", "access check"],
        ):
            code, output = _run(command, cwd=work, env=env)
            if code != 0:
                raise RemoteError(f"Local write check preparation failed: {output}")
        code, output = _run(
            ["git", "push", "--dry-run", url, "HEAD:refs/heads/caliper-access-check"],
            cwd=work,
            env=env,
        )
    if code != 0:
        raise RemoteError(f"Write check failed: {output or 'git push --dry-run failed'}")
    return "Read and write access verified."


def ssh_copy_id_command(key_path: Path, user: str, host: str) -> str:
    """The exact command the human runs once to authorize Caliper's key."""
    path = str(key_path)
    quoted = f'"{path}"' if " " in path else path
    return f"ssh-copy-id -i {quoted} {user}@{host}"


def _fingerprint_of(key_line: str) -> str:
    """OpenSSH-style SHA256 fingerprint of a public key line."""
    parts = key_line.split()
    blob = None
    for index, part in enumerate(parts):
        if part in HOST_KEY_TYPES and index + 1 < len(parts):
            blob = parts[index + 1]
            break
    if blob is None:
        raise RemoteError("The host key could not be read.")
    try:
        raw = base64.b64decode(blob, validate=True)
    except Exception:
        raise RemoteError("The host key could not be read.")
    digest = hashlib.sha256(raw).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _host_key_failure(host: str, output: str) -> str:
    """Plain-language reason for a host key that could not be read."""
    text = (output or "").lower()
    if "no such host" in text or "not known" in text or "name or service not known" in text:
        return f"The host {host} does not resolve. Check the host name."
    if "connection refused" in text:
        return f"Nothing is listening for SSH on {host}. Check the host and the firewall."
    if "timed out" in text or "timeout" in text:
        return f"The host {host} did not answer in time. Check the host and the firewall."
    lines = list(dict.fromkeys(line.strip() for line in (output or "").splitlines() if line.strip()))
    return f"Cannot read the host key of {host}: {' '.join(lines) or 'ssh-keyscan failed'}"


def scan_host_key(host: str, port: int = 22) -> tuple[str, str]:
    """Read the host key of a machine. Returns (fingerprint, key line)."""
    keyscan = shutil.which("ssh-keyscan")
    if not keyscan:
        raise RemoteError("ssh-keyscan is not installed; the host key cannot be pinned.")
    if not host or any(ch.isspace() for ch in host):
        raise RemoteError("Host is required and may not contain spaces.")
    command = [keyscan, "-T", "10", "-t", ",".join(HOST_KEY_TYPES)]
    if port and int(port) != 22:
        command += ["-p", str(port)]
    command.append(host)
    code, output = _run(command)
    lines = []
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[1] in HOST_KEY_TYPES:
            lines.append(line.strip())
    if not lines:
        raise RemoteError(_host_key_failure(host, output))
    for line in lines:
        if line.split()[1] == "ssh-ed25519":
            return _fingerprint_of(line), line
    return _fingerprint_of(lines[0]), lines[0]


def pinned_fingerprints(known_hosts_path: Path) -> list[str]:
    """Fingerprints of the host keys already pinned in a known_hosts file."""
    path = Path(known_hosts_path)
    if not path.is_file():
        return []
    fingerprints = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            fingerprints.append(_fingerprint_of(line))
        except RemoteError:
            continue
    return fingerprints


def pin_host_key(host: str, known_hosts_path: Path, port: int = 22) -> dict:
    """Pin the host key on first use. A changed host key fails loudly."""
    fingerprint, key_line = scan_host_key(host, port)
    path = Path(known_hosts_path)
    existing = pinned_fingerprints(path)
    if existing:
        if fingerprint not in existing:
            raise RemoteError(
                f"The host key of {host} changed. Caliper pinned {existing[0]} and the machine now "
                f"offers {fingerprint}. Remove the stale entry with: "
                f"ssh-keygen -f \"{path}\" -R {host}"
            )
        return {"fingerprint": fingerprint, "known_hosts_path": str(path), "pinned": False}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key_line.strip() + "\n", encoding="utf-8")
    return {"fingerprint": fingerprint, "known_hosts_path": str(path), "pinned": True}


def _ssh_base(target: str, key_path: Path, known_hosts_path: Path | None) -> list[str]:
    base = [
        "ssh", "-i", str(key_path), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15",
    ]
    if known_hosts_path is not None:
        base += ["-o", f"UserKnownHostsFile={known_hosts_path}"]
    base.append(target)
    return base


def _ssh_failure(target: str, output: str, key_path: Path) -> str:
    text = (output or "").lower()
    user, _, host = target.partition("@")
    if "permission denied" in text or "publickey" in text:
        return (
            f"SSH to {target} was refused: the key is not authorized on the host. "
            f"Authorize it with: {ssh_copy_id_command(key_path, user, host)}"
        )
    if "host key verification failed" in text:
        return (
            f"SSH to {target} failed: the host key does not match the pinned key. "
            "Check that the machine was not rebuilt, then remove the stale entry from the known hosts file."
        )
    if "connection refused" in text:
        return f"SSH to {target} was refused: nothing is listening for SSH on that host."
    if "timed out" in text or "timeout" in text:
        return f"SSH to {target} timed out. Check the host address and the firewall."
    if "could not resolve" in text or "name or service not known" in text:
        return f"SSH to {target} failed: the host name does not resolve."
    return f"SSH to {target} failed: {output or 'ssh returned an error'}"


def test_environment(
    host: str,
    user: str,
    key_path: Path,
    deploy_path: str = "",
    compose_file: str = "",
    known_hosts_path: Path | None = None,
    port: int = 22,
) -> dict:
    """Check SSH, Docker Compose and the deploy path of an environment.

    Returns a result dict with the message and the pinned host key fingerprint.
    """
    if not Path(key_path).is_file():
        raise RemoteError("Save the environment first so Caliper can create its SSH key.")
    if not host or not user or any(ch.isspace() for ch in host + user):
        raise RemoteError("Host and user are required and may not contain spaces.")
    target = f"{user}@{host}"
    pinned = {}
    if known_hosts_path is not None:
        pinned = pin_host_key(host, known_hosts_path, port)
    base = _ssh_base(target, key_path, known_hosts_path)
    code, output = _run(base + ["true"])
    if code != 0:
        raise RemoteError(_ssh_failure(target, output, key_path))
    code, output = _run(base + ["docker compose version"])
    if code != 0:
        raise RemoteError(
            f"Connected to {target}, but 'docker compose version' failed: {output or 'no output'}. "
            "Install Docker Compose v2 on the host and test again."
        )
    if deploy_path:
        quoted = shlex.quote(deploy_path)
        code, output = _run(base + [f"test -d {quoted} && test -w {quoted}"])
        if code != 0:
            raise RemoteError(
                f"The deploy path {deploy_path} does not exist on {target} or is not writable by {user}. "
                f"Create it and give {user} access with: ssh {target} 'mkdir -p {deploy_path}'"
            )
    return {
        "message": "SSH, docker compose and the deploy path are ready.",
        "host_key_fingerprint": pinned.get("fingerprint"),
        "host_key_pinned": bool(pinned.get("pinned")),
    }


def stop_environment(
    host: str,
    user: str,
    deploy_path: str,
    compose_file: str,
    key_path: Path,
    known_hosts_path: Path | None = None,
) -> str:
    if not Path(key_path).is_file():
        raise RemoteError("The environment SSH key is missing; delete cannot stop this deployment.")
    if not host or not user or any(ch.isspace() for ch in host + user):
        raise RemoteError("Host and user are required and may not contain spaces.")
    if not deploy_path or not compose_file:
        raise RemoteError("Deploy path and compose file are required to stop this deployment.")
    target = f"{user}@{host}"
    if known_hosts_path is not None:
        pin_host_key(host, known_hosts_path)
    base = _ssh_base(target, key_path, known_hosts_path)
    command = f"cd {shlex.quote(deploy_path)} && docker compose -f {shlex.quote(compose_file)} down"
    code, output = _run(base + [command])
    if code != 0:
        raise RemoteError(f"docker compose down failed: {output or 'ssh returned an error'}")
    return "Deployment stopped. Volumes kept."
