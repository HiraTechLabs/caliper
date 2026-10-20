"""Git access for a project repository.

The project repository is the source of truth for the project's ``.caliper/`` files
(principles, decisions, graph). Caliper keeps one local clone per project and talks to
the remote only through the git CLI, using the project's deploy key.
"""

import re
from pathlib import Path

from caliper.remote import _git_env, _run, ssh_url

GIT_IDENTITY = ["-c", "user.name=Caliper", "-c", "user.email=caliper@localhost"]


class RepoError(Exception):
    pass


class GitRepo:
    """A local clone of one project repository."""

    def __init__(self, root: Path, url: str, key_path: Path):
        self.root = Path(root)
        self.url = url
        self.key_path = Path(key_path)

    @property
    def cloned(self) -> bool:
        return (self.root / ".git").is_dir()

    def _git(self, args: list[str], cwd: Path | None = None) -> tuple[int, str]:
        return _run(["git", *args], cwd=cwd or self.root, env=_git_env(self.key_path))

    def remote_url(self) -> str:
        try:
            return ssh_url(self.url)
        except Exception as exc:
            raise RepoError(str(exc))

    def default_branch(self) -> str:
        """The branch the remote points HEAD at."""
        code, output = self._git(
            ["ls-remote", "--symref", self.remote_url(), "HEAD"],
            cwd=self.root if self.cloned else self.root.parent,
        )
        if code != 0:
            raise RepoError(f"Cannot read the repository: {output or 'git ls-remote failed'}")
        match = re.search(r"ref:\s+refs/heads/([^\s]+)\s+HEAD", output)
        if match:
            return match.group(1)
        match = re.search(r"refs/heads/([^\s]+)", output)
        if match:
            return match.group(1)
        return "main"

    def ensure_clone(self) -> str:
        """Clone the repository if it is not there yet. Returns the default branch."""
        branch = self.default_branch()
        if self.cloned:
            return branch
        self.root.parent.mkdir(parents=True, exist_ok=True)
        code, output = self._git(
            ["clone", "--depth", "1", "--branch", branch, self.remote_url(), str(self.root)],
            cwd=self.root.parent,
        )
        if code != 0:
            raise RepoError(f"Cannot clone the repository: {output or 'git clone failed'}")
        return branch

    def sync(self) -> str:
        """Fetch the default branch and make the working copy match it."""
        branch = self.default_branch()
        if not self.cloned:
            self.ensure_clone()
            return branch
        code, output = self._git(["fetch", "--depth", "1", "origin", branch])
        if code != 0:
            raise RepoError(f"Cannot fetch the repository: {output or 'git fetch failed'}")
        code, output = self._git(["checkout", "--force", "-B", branch, f"origin/{branch}"])
        if code != 0:
            raise RepoError(f"Cannot check out {branch}: {output or 'git checkout failed'}")
        return branch

    def head_commit(self) -> str:
        code, output = self._git(["rev-parse", "HEAD"])
        if code != 0:
            raise RepoError(f"Cannot read the current commit: {output or 'git rev-parse failed'}")
        return output.strip()

    def read(self, relative: str) -> str | None:
        """Content of a file in the working copy, or None when it does not exist."""
        path = self._resolve(relative)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def write(self, relative: str, content: str, message: str) -> str:
        """Write a file, commit it and push it to the default branch. Returns the commit."""
        path = self._resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        code, output = self._git(["add", "--", relative])
        if code != 0:
            raise RepoError(f"Cannot stage {relative}: {output or 'git add failed'}")
        code, output = self._git([*GIT_IDENTITY, "commit", "-m", message])
        if code != 0:
            if "nothing to commit" in output.lower():
                raise RepoError("The file is unchanged; nothing was committed.")
            raise RepoError(f"Cannot commit {relative}: {output or 'git commit failed'}")
        branch = self.default_branch()
        code, output = self._git(["push", "origin", f"HEAD:refs/heads/{branch}"])
        if code != 0:
            raise RepoError(
                f"Cannot push to {branch}: {output or 'git push failed'}. "
                "Pull the latest changes and try again."
            )
        return self.head_commit()

    def _resolve(self, relative: str) -> Path:
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise RepoError("Repository paths must be relative and inside the repository.")
        path = (self.root / relative).resolve()
        root = self.root.resolve()
        if root != path and root not in path.parents:
            raise RepoError("Repository paths must stay inside the repository.")
        return path
