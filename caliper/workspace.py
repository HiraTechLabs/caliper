"""Real Git workspace for one request.

The workflow never edits the project's default branch. It clones/syncs the
project repository once (the existing ``GitRepo``), creates one worktree per
request on ``caliper/<request id>`` (D-046), resets that worktree to the
recorded base commit before every attempt, computes the authoritative diff from
Git, runs the project's test command, and merges the reviewed commit only after
human approval (D-047..D-050).

``patch_safety`` remains the only code path that applies a model-produced
diff. The functions here add branch/worktree lifecycle, the test gate and the
real merge; they never trust a model's claim that a change was applied.
"""

import hashlib
import re
import subprocess
from pathlib import Path

import yaml

from caliper.patch_safety import (
    MAX_PATCH_CHARS,
    PatchError,
    apply_patch,
    create_worktree,
    remove_worktree,
    validate_patch,
)
from caliper.remote import _run
from caliper.repo import GIT_IDENTITY, GitRepo, RepoError

TEST_TIMEOUT = 900
TEST_OUTPUT_LIMIT = 20_000
MAX_GIT_OUTPUT = MAX_PATCH_CHARS + 10_000
CONTEXT_TOTAL_LIMIT = 200_000
CONTEXT_FILE_LIMIT = 40_000
CONTEXT_FILE_COUNT = 20
TREE_LIMIT = 300

_CODE_PATH_RE = re.compile(
    r"(?<![\w./-])((?:\.?[\w-]+/)*[\w.-]+\.(?:py|js|jsx|ts|tsx|md|html|css|json|ya?ml|toml|txt|sh|cfg|ini))"
)


class WorkspaceError(Exception):
    """A real Git/workspace operation failed; the request must not report success."""


class RequestWorkspaceService:
    def __init__(self, registry, test_timeout: float = TEST_TIMEOUT):
        self.registry = registry
        self.test_timeout = test_timeout

    # --- paths and repository -------------------------------------------------
    def worktrees_root(self, project_id: str) -> Path:
        return Path(self.registry.work_root) / project_id

    def worktree_path(self, project_id: str, req_id: str) -> Path:
        return self.worktrees_root(project_id) / req_id

    def _repo(self, project_id: str) -> GitRepo:
        manifest = self.registry.manifest(project_id)
        repo = manifest.get("repo") or {}
        url = (repo.get("url") or "").strip()
        if not url:
            raise WorkspaceError("The project has no repository URL. Add one before running a request.")
        # A file:// remote is a local test/offline repository and does not use SSH.
        if url.startswith("file://"):
            return GitRepo(self.registry.project_path(project_id) / "repo", url)
        if not self.registry.deploy_key_exists(project_id):
            raise WorkspaceError(
                "Generate the project's repository deploy key and test read/write access before running a request."
            )
        return GitRepo(
            self.registry.project_path(project_id) / "repo",
            url,
            key_context=lambda: self.registry.deploy_key_context(project_id),
        )

    def prepare(self, project_id: str, req_id: str) -> dict:
        """Clone/sync the default branch and create a clean worktree for one request."""
        repo = self._repo(project_id)
        try:
            repo.ensure_clone()
            default_branch = repo.sync()
        except RepoError as exc:
            raise WorkspaceError(str(exc))
        self.discard(project_id, req_id)
        try:
            info = create_worktree(repo.root, req_id, self.worktrees_root(project_id))
        except PatchError as exc:
            raise WorkspaceError(str(exc))
        path = Path(info["path"])
        base_commit = self.head(path)
        return {
            "project_id": project_id,
            "request_id": req_id,
            "branch": info["branch"],
            "path": str(path),
            "base_commit": base_commit,
            "default_branch": default_branch,
        }

    def discard(self, project_id: str, req_id: str) -> None:
        """Remove a request worktree and branch; safe when they do not exist."""
        repo_root = self.registry.project_path(project_id) / "repo"
        if not (repo_root / ".git").is_dir():
            return
        try:
            remove_worktree(repo_root, req_id, self.worktrees_root(project_id))
        except PatchError as exc:
            raise WorkspaceError(str(exc))

    def _require_path(self, path: str | Path) -> Path:
        worktree = Path(path)
        if not worktree.is_dir():
            raise WorkspaceError(f"The request worktree '{worktree}' does not exist. Run the request again.")
        return worktree

    # --- attempt lifecycle ----------------------------------------------------
    def _git(self, worktree: Path, args: list[str], timeout: float = 120,
             max_output: int = MAX_GIT_OUTPUT) -> tuple[int, str]:
        return _run(["git", *args], cwd=worktree, timeout=timeout, max_output=max_output)

    def reset(self, path: str | Path, base_commit: str) -> None:
        """Discard the previous attempt and return the worktree to its base commit."""
        worktree = self._require_path(path)
        code, output = self._git(worktree, ["reset", "--hard", base_commit])
        if code != 0:
            raise WorkspaceError(f"Cannot reset the request worktree to {base_commit}: {output or 'git reset failed'}")
        code, output = self._git(worktree, ["clean", "-fd"])
        if code != 0:
            raise WorkspaceError(f"Cannot clean the request worktree: {output or 'git clean failed'}")

    def apply(self, diff: str, path: str | Path) -> None:
        """Validate and apply a model-produced diff inside the worktree."""
        worktree = self._require_path(path)
        try:
            apply_patch(diff, worktree)
        except PatchError as exc:
            raise WorkspaceError(str(exc))

    def stage_and_diff(self, path: str | Path, base_commit: str) -> str:
        """Stage the worktree and return the authoritative diff from the base commit.

        ``validate_patch`` rejects an empty, overlarge, binary, traversal or
        Git-internal change before Caliper commits anything.
        """
        worktree = self._require_path(path)
        code, output = self._git(worktree, ["add", "-A"])
        if code != 0:
            raise WorkspaceError(f"Cannot stage the change: {output or 'git add failed'}")
        code, diff = self._git(
            worktree,
            ["diff", "--cached", "--no-color", "--no-ext-diff", base_commit],
        )
        if code != 0:
            raise WorkspaceError(f"Cannot compute the change: {diff or 'git diff failed'}")
        try:
            validate_patch(diff)
        except PatchError as exc:
            raise WorkspaceError(str(exc))
        return diff

    def commit(self, path: str | Path, message: str) -> str:
        worktree = self._require_path(path)
        code, output = self._git(worktree, [*GIT_IDENTITY, "commit", "-m", message])
        if code != 0:
            raise WorkspaceError(f"Cannot commit the change: {output or 'git commit failed'}")
        return self.head(worktree)

    def head(self, path: str | Path) -> str:
        worktree = self._require_path(path)
        code, output = self._git(worktree, ["rev-parse", "HEAD"])
        if code != 0:
            raise WorkspaceError(f"Cannot read the current commit: {output or 'git rev-parse failed'}")
        return output.strip()

    def diff_between(self, path: str | Path, base_commit: str, commit: str) -> str:
        worktree = self._require_path(path)
        code, diff = self._git(worktree, ["diff", "--no-color", "--no-ext-diff", base_commit, commit])
        if code != 0:
            raise WorkspaceError(f"Cannot read the committed change: {diff or 'git diff failed'}")
        return diff

    @staticmethod
    def diff_hash(diff: str) -> str:
        return hashlib.sha256((diff or "").encode("utf-8")).hexdigest()

    def clean_untracked(self, path: str | Path) -> None:
        worktree = self._require_path(path)
        code, output = self._git(worktree, ["clean", "-fd"])
        if code != 0:
            raise WorkspaceError(f"Cannot clean test artifacts from the worktree: {output or 'git clean failed'}")

    def status(self, path: str | Path) -> str:
        worktree = self._require_path(path)
        code, output = self._git(worktree, ["status", "--porcelain"])
        if code != 0:
            raise WorkspaceError(f"Cannot inspect the worktree: {output or 'git status failed'}")
        return output.strip()

    # --- test gate ------------------------------------------------------------
    def _parse_test_command(self, text: str) -> str | None:
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise WorkspaceError(f".caliper/manifest.yaml is not valid YAML: {exc}")
        if not isinstance(data, dict):
            raise WorkspaceError(".caliper/manifest.yaml must be a YAML mapping.")
        command = data.get("test_command")
        if command is None:
            return None
        if not isinstance(command, str):
            raise WorkspaceError("test_command in .caliper/manifest.yaml must be one shell command string.")
        return command.strip() or None

    def test_command(self, path: str | Path) -> str | None:
        """The project's test command from ``.caliper/manifest.yaml`` in the working tree."""
        worktree = self._require_path(path)
        manifest_path = worktree / ".caliper" / "manifest.yaml"
        if not manifest_path.is_file():
            return None
        return self._parse_test_command(manifest_path.read_text(encoding="utf-8"))

    def test_command_at(self, path: str | Path, commit: str) -> str | None:
        """The test command at a commit, so a change cannot weaken its own gate."""
        worktree = self._require_path(path)
        code, output = self._git(
            worktree, ["show", f"{commit}:.caliper/manifest.yaml"], max_output=200_000
        )
        if code != 0:
            if "does not exist" in output or "exists on disk, but not in" in output:
                return None
            raise WorkspaceError(f"Cannot read .caliper/manifest.yaml at {commit}: {output}")
        return self._parse_test_command(output)

    def run_tests(self, path: str | Path, command: str) -> tuple[bool, str]:
        """Run the project's test command in the worktree, never in Caliper's directory."""
        worktree = self._require_path(path)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(worktree),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.test_timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"The test command timed out after {self.test_timeout:g} seconds: {command}"
        except OSError as exc:
            return False, f"The test command could not be started: {exc}"
        output = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        output = output[-TEST_OUTPUT_LIMIT:] if output else "(the test command produced no output)"
        return completed.returncode == 0, output

    # --- approval evidence and merge -----------------------------------------
    def verify_reviewed(self, path: str | Path, base_commit: str, commit: str, expected_diff_hash: str) -> None:
        """Refuse approval unless the worktree still holds exactly the reviewed change."""
        worktree = self._require_path(path)
        current = self.head(worktree)
        if current != commit:
            raise WorkspaceError(
                f"The request branch changed after review (reviewed {commit}, found {current}). "
                "Run the request again or request changes so the new change is reviewed."
            )
        diff = self.diff_between(worktree, base_commit, commit)
        if self.diff_hash(diff) != expected_diff_hash:
            raise WorkspaceError(
                "The reviewed diff no longer matches the request branch. "
                "Run the request again or request changes so the new change is reviewed."
            )
        status = self.status(worktree)
        if status:
            raise WorkspaceError(
                "The request worktree has uncommitted changes that were not reviewed. "
                "Run the request again or request changes."
            )

    def merge(self, project_id: str, branch: str, expected_commit: str,
              worktrees_root: Path | None = None) -> dict:
        """Merge the reviewed branch into the freshly synced default branch and push it."""
        repo = self._repo(project_id)
        try:
            repo.ensure_clone()
            default_branch = repo.sync()
        except RepoError as exc:
            raise WorkspaceError(str(exc))
        code, output = _run(["git", "rev-parse", branch], cwd=repo.root, max_output=4000)
        if code != 0:
            raise WorkspaceError(f"The request branch {branch} no longer exists: {output or 'git rev-parse failed'}")
        if output.strip() != expected_commit:
            raise WorkspaceError(
                f"The request branch changed after review (reviewed {expected_commit}, found {output.strip()})."
            )
        code, output = _run(
            ["git", *GIT_IDENTITY, "merge", "--no-ff", "--no-edit", branch],
            cwd=repo.root,
            timeout=180,
            max_output=8000,
        )
        if code != 0:
            _run(["git", "merge", "--abort"], cwd=repo.root, timeout=60)
            detail = " ".join(line.strip() for line in output.splitlines() if line.strip())
            raise WorkspaceError(
                f"The change conflicts with the default branch {default_branch}: {detail or 'git merge failed'}. "
                "The merge was aborted; nothing was pushed."
            )
        merge_commit = _run(["git", "rev-parse", "HEAD"], cwd=repo.root, max_output=200)[1].strip()
        code, output = repo.run(
            ["push", "origin", f"HEAD:refs/heads/{default_branch}"],
            timeout=180,
            max_output=8000,
        )
        if code != 0:
            _run(["git", "reset", "--hard", f"origin/{default_branch}"], cwd=repo.root, timeout=120)
            detail = " ".join(line.strip() for line in output.splitlines() if line.strip())
            raise WorkspaceError(
                f"The merge commit could not be pushed to {default_branch}: {detail or 'git push failed'}. "
                "The local default branch was reset; nothing was pushed."
            )
        return {"merge_commit": merge_commit, "default_branch": default_branch}

    # --- context for a tool-less HTTP execution model -------------------------
    def context(self, path: str | Path, plan: str) -> str:
        """A size-capped tree plus the files the plan names, for an HTTP model."""
        worktree = self._require_path(path)
        code, tree = self._git(worktree, ["ls-files"], max_output=50_000)
        if code != 0:
            tree = ""
        listed = [line.strip() for line in tree.splitlines() if line.strip()]
        tree_text = "\n".join(listed[:TREE_LIMIT])
        if len(listed) > TREE_LIMIT:
            tree_text += f"\n... ({len(listed) - TREE_LIMIT} more files)"

        parts = ["Repository files:\n" + (tree_text or "(no tracked files)")]
        seen: set[str] = set()
        total = len(parts[0])
        for match in _CODE_PATH_RE.finditer(plan or ""):
            relative = match.group(1).lstrip("./")
            if not relative or relative in seen:
                continue
            candidate = (worktree / relative).resolve()
            root = worktree.resolve()
            if root != candidate and root not in candidate.parents:
                continue
            if not candidate.is_file():
                continue
            seen.add(relative)
            if len(seen) > CONTEXT_FILE_COUNT or total >= CONTEXT_TOTAL_LIMIT:
                break
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            content = content[:CONTEXT_FILE_LIMIT]
            block = f"\n\nFile: {relative}\n```\n{content}\n```"
            if total + len(block) > CONTEXT_TOTAL_LIMIT:
                break
            parts.append(block)
            total += len(block)
        return "".join(parts)
