import subprocess
from pathlib import Path

import pytest

from caliper.patch_safety import (
    PatchError,
    apply_patch,
    create_worktree,
    remove_worktree,
    request_branch,
    validate_patch,
)

SIMPLE_PATCH = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-# project
+# changed
"""

NEW_FILE_PATCH = """diff --git a/src/app.py b/src/app.py
new file mode 100644
--- /dev/null
+++ b/src/app.py
@@ -0,0 +1 @@
+print("hello")
"""


def _work_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    (path / "README.md").write_text("# project\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=Caliper", "-c", "user.email=caliper@localhost",
         "commit", "-qm", "init"],
        check=True, capture_output=True,
    )
    return path


def test_validate_returns_the_touched_files():
    assert validate_patch(SIMPLE_PATCH) == ["README.md"]


def test_validate_accepts_a_new_file():
    assert validate_patch(NEW_FILE_PATCH) == ["src/app.py"]


def test_validate_rejects_an_empty_patch():
    with pytest.raises(PatchError, match="empty"):
        validate_patch("   ")


def test_validate_rejects_output_that_is_not_a_diff():
    with pytest.raises(PatchError, match="not a unified diff"):
        validate_patch("I changed the file, it should work now.")


def test_validate_rejects_absolute_paths():
    patch = SIMPLE_PATCH.replace("README.md", "/etc/passwd")
    with pytest.raises(PatchError, match="outside the project"):
        validate_patch(patch)


def test_validate_rejects_path_traversal():
    patch = SIMPLE_PATCH.replace("README.md", "../../escape.md")
    with pytest.raises(PatchError, match="outside the project root"):
        validate_patch(patch)


def test_validate_rejects_binary_changes():
    patch = SIMPLE_PATCH + "GIT binary patch\n"
    with pytest.raises(PatchError, match="binary"):
        validate_patch(patch)


def test_validate_rejects_a_slash_header_pair():
    patch = "--- a/README.md\n@@ -1 +1 @@\n-x\n+y\n"
    with pytest.raises(PatchError, match="without a"):
        validate_patch(patch)


def test_request_branch_names_are_constrained():
    assert request_branch("req-7") == "caliper/req-7"
    with pytest.raises(PatchError, match="branch name"):
        request_branch("req ../7")


def test_apply_changes_the_file_inside_the_target(tmp_path):
    root = _work_repo(tmp_path / "work")
    touched = apply_patch(SIMPLE_PATCH, root)
    assert touched == ["README.md"]
    assert (root / "README.md").read_text(encoding="utf-8").startswith("# changed")


def test_apply_rejects_a_target_that_is_not_a_working_copy(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(PatchError, match="not a git working copy"):
        apply_patch(SIMPLE_PATCH, plain)


def test_a_failing_check_never_touches_the_files(tmp_path):
    root = _work_repo(tmp_path / "work")
    broken = SIMPLE_PATCH.replace("-# project", "-# something else entirely")
    with pytest.raises(PatchError, match="does not apply"):
        apply_patch(broken, root)
    assert (root / "README.md").read_text(encoding="utf-8") == "# project\n"


def test_worktree_lifecycle_isolates_one_request_branch(tmp_path):
    root = _work_repo(tmp_path / "work")
    worktrees = tmp_path / "worktrees"
    info = create_worktree(root, "req-1", worktrees)
    assert info["branch"] == "caliper/req-1"
    checkout = Path(info["path"])
    assert (checkout / "README.md").is_file()
    active = subprocess.run(
        ["git", "-C", str(checkout), "branch", "--show-current"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert active == "caliper/req-1"

    # Changes stay inside the worktree; the main clone is not touched.
    apply_patch(SIMPLE_PATCH, checkout)
    assert (checkout / "README.md").read_text(encoding="utf-8").startswith("# changed")
    assert (root / "README.md").read_text(encoding="utf-8") == "# project\n"

    remove_worktree(root, "req-1", worktrees)
    assert not checkout.exists()
    branches = subprocess.run(
        ["git", "-C", str(root), "branch", "--list", "caliper/req-1"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert branches == ""


def test_worktree_creation_refuses_an_existing_directory(tmp_path):
    root = _work_repo(tmp_path / "work")
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    (worktrees / "req-1").mkdir()
    with pytest.raises(PatchError, match="already exists"):
        create_worktree(root, "req-1", worktrees)


def test_worktree_creation_refuses_a_non_repository(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(PatchError, match="not a git working copy"):
        create_worktree(plain, "req-1", tmp_path / "worktrees")
