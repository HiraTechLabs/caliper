"""Safe handling of model-produced unified diffs.

``RequestWorkspaceService`` uses this module for every model-produced change
(see D-046 and D-047):

- ``validate_patch`` statically rejects absolute paths, path traversal, binary
  changes, ``.git`` internals, and empty or malformed output before anything
  touches the disk.
- ``apply_patch`` applies a validated diff through ``git apply`` (arranged check
  first, then apply) inside one target worktree, via an argv array with a
  bounded timeout — never a shell.
- ``create_worktree`` / ``remove_worktree`` isolate one request on the branch
  ``caliper/<request id>`` of a project clone (AGENTS.md 7b: one branch per
  request, never the default branch).

Known limitation, recorded in D-031: symlink and rename oddities rely on
``git apply``'s own defaults.
"""

import re
from pathlib import Path

from caliper.remote import _run

MAX_PATCH_CHARS = 2_000_000
_BINARY_MARKERS = ("GIT binary patch", "Binary files")


class PatchError(Exception):
    pass


def request_branch(req_id: str) -> str:
    """The isolated branch name for one request."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", req_id):
        raise PatchError(f"Request id '{req_id}' cannot become a branch name.")
    return f"caliper/{req_id}"


def _unquote(token: str) -> str:
    token = token.strip()
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        token = token[1:-1].encode("utf-8").decode("unicode_escape")
    return token


def _patch_target(line: str, side: str) -> str | None:
    """The real path behind a ``---``/``+++`` line; None for /dev/null."""
    token = line[len(side):].split("\t")[0].strip()
    token = _unquote(token)
    if token == "/dev/null":
        return None
    for prefix in ("a/", "b/"):
        if token.startswith(prefix):
            token = token[len(prefix):]
            break
    return token


def _check_target(path: str) -> None:
    """Reject anything that would leave the project root or is not a plain path."""
    if not path:
        raise PatchError("The patch contains an empty file path.")
    if path.startswith("/"):
        raise PatchError(f"The patch modifies an absolute path outside the project: {path}")
    if "\\" in path:
        raise PatchError(f"The patch contains a non-portable path with a backslash: {path}")
    parts = Path(path).parts
    if not parts or any(part == ".." for part in parts):
        raise PatchError(f"The patch modifies a path outside the project root: {path}")
    if any(part in ("", ".") for part in parts[1:]):
        raise PatchError(f"The patch contains an unusual path component: {path}")
    if any(part == ".git" for part in parts):
        raise PatchError(f"The patch may not modify Git internals: {path}")


def _collect_paths(diff: str) -> list[str]:
    if not diff.strip():
        raise PatchError("The patch is empty; the model produced no change.")
    if len(diff) > MAX_PATCH_CHARS:
        raise PatchError("The patch exceeds the maximum size and is rejected.")
    lines = diff.splitlines()
    headers = [i for i, line in enumerate(lines) if line.startswith("--- ")]
    if not headers:
        raise PatchError("The patch is not a unified diff (no ---/+++ headers).")
    for line in lines:
        for marker in _BINARY_MARKERS:
            if marker in line:
                raise PatchError(
                    "The patch contains binary changes, which are not accepted. "
                    "Ask again for a text-only change."
                )
    targets: list[str] = []
    for index in headers:
        target = _patch_target(lines[index], "--- ")
        if target is not None:
            targets.append(target)
        if index + 1 < len(lines) and lines[index + 1].startswith("+++ "):
            target = _patch_target(lines[index + 1], "+++ ")
            if target is not None:
                targets.append(target)
        else:
            raise PatchError("The patch is not a unified diff (a --- header without a +++ header).")
    seen: list[str] = []
    for target in targets:
        if target not in seen:
            seen.append(target)
    if not seen:
        raise PatchError("The patch adds nothing to the project (only deletions to /dev/null).")
    for target in seen:
        _check_target(target)
    return seen


def validate_patch(diff: str) -> list[str]:
    """Check a model-produced unified diff; returns the files it touches.

    Raises PatchError with a plain-language reason when anything unsafe or
    unapplicable is found.
    """
    return _collect_paths(diff)


def apply_patch(diff: str, root: Path, timeout: float = 60) -> list[str]:
    """Apply a validated diff inside one worktree through the git CLI.

    Runs ``git apply --check`` first; only a dry run that passes is applied.
    Returns the touched files.
    """
    targets = validate_patch(diff)
    root = Path(root)
    if not (root / ".git").exists():
        raise PatchError(f"The target '{root}' is not a git working copy or worktree.")
    code, output = _run(
        ["git", "apply", "--check", "--whitespace=warn", "-"], cwd=root, timeout=timeout, input_text=diff
    )
    if code != 0:
        raise PatchError(f"The patch does not apply: {output or 'git apply --check failed'}")
    code, output = _run(["git", "apply", "--whitespace=nowarn", "-"], cwd=root, timeout=timeout, input_text=diff)
    if code != 0:
        raise PatchError(f"The patch failed to apply: {output or 'git apply failed'}")
    return targets


def create_worktree(repo_root: Path, req_id: str, worktrees_root: Path, timeout: float = 45) -> dict:
    """Add an isolated worktree with one branch per request.

    The branch is ``caliper/<request id>``, cut from the clone's HEAD (the
    default branch is never checked out for editing).
    """
    branch = request_branch(req_id)
    repo_root = Path(repo_root)
    if not (repo_root / ".git").is_dir():
        raise PatchError(f"The target '{repo_root}' is not a git working copy.")
    worktrees_root = Path(worktrees_root)
    worktrees_root.mkdir(parents=True, exist_ok=True)
    path = worktrees_root / req_id
    if path.exists() or path.is_symlink():
        raise PatchError(f"The worktree '{path}' already exists.")
    code, output = _run(["git", "worktree", "add", "-b", branch, str(path)], cwd=repo_root, timeout=timeout)
    if code != 0:
        raise PatchError(f"Cannot create the isolated worktree for {branch}: {output or 'git worktree add failed'}")
    return {"request_id": req_id, "branch": branch, "path": str(path)}


def remove_worktree(repo_root: Path, req_id: str, worktrees_root: Path, timeout: float = 45) -> None:
    """Remove one isolated worktree (and its branch) after a request ends.

    ``worktrees_root`` must be the same root that ``create_worktree`` used.
    Safe to call for a request that has no worktree or branch.
    """
    repo_root = Path(repo_root)
    branch = request_branch(req_id)
    path = Path(worktrees_root) / req_id
    _run(["git", "worktree", "prune"], cwd=repo_root, timeout=timeout)
    if path.is_symlink():
        raise PatchError(f"The worktree path is a symlink; refusing to remove it: {path}")
    if path.exists():
        code, output = _run(["git", "worktree", "remove", "--force", str(path)], cwd=repo_root, timeout=timeout)
        if code != 0 and "already removed" not in output.lower() and not path.exists():
            raise PatchError(f"Cannot remove the worktree for {branch}: {output or 'git worktree remove failed'}")
    code, output = _run(["git", "branch", "--list", branch], cwd=repo_root, timeout=timeout)
    if code != 0:
        raise PatchError(f"Cannot inspect the branch {branch}: {output or 'git branch --list failed'}")
    if output.strip():
        code, output = _run(["git", "branch", "-D", branch], cwd=repo_root, timeout=timeout)
        if code != 0:
            raise PatchError(f"Cannot delete the branch {branch}: {output or 'git branch -D failed'}")
