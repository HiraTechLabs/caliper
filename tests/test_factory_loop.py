"""End-to-end tests for the real factory loop (Phase 0c).

Every test here uses a real temporary Git repository (a bare remote plus a
working clone) and a real worktree. No mock Git and no simulated merge: the
assertions read the remote with ``git show``.
"""

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from caliper.main import create_app
from caliper.models_config import ROLE_ORDER, make_http_adapters
from caliper.opencode_models import OpenCodeClient, OpenCodeExecutionModel
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore
from caliper.workflow import MAX_LOOPS, Workflow, WorkflowError
from tests.mock_models import MockExecutionModel, MockPlanningModel, MockReviewModel
from tests.support import attach_real_repo, complete_setup

# --- helpers ---------------------------------------------------------------

def _new_file_patch(name: str, content: str) -> str:
    return (
        f"--- /dev/null\n+++ b/{name}\n"
        f"@@ -0,0 +1 @@\n+{content}\n"
    )


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=False
    )


def _remote_file(remote: Path, relative: str, branch: str = "main") -> str | None:
    result = _git(remote, "show", f"{branch}:{relative}")
    return result.stdout if result.returncode == 0 else None


def _remote_head(remote: Path, branch: str = "main") -> str:
    result = _git(remote, "rev-parse", branch)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class ScriptedExecution:
    """Returns the next diff on each call and records the feedback it saw."""

    def __init__(self, diffs: list[str]):
        self.diffs = diffs
        self.calls = 0
        self.feedback = []

    def execute(self, req, project, plan, feedback=None):
        self.feedback.append(feedback)
        index = min(self.calls, len(self.diffs) - 1)
        self.calls += 1
        return self.diffs[index]


class RecordingReview(MockReviewModel):
    def __init__(self):
        self.seen = []

    def review(self, req, project, plan, diff, rule_ids, tests=None):
        self.seen.append({"diff": diff, "tests": tests})
        return super().review(req, project, plan, diff, rule_ids, tests=tests)


class RejectingReview(MockReviewModel):
    force_fail = True


def make_env(tmp_path, *, execution=None, review=None, test_command="true", files=None):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    attach_real_repo(tmp_path, registry, "shop-api", test_command=test_command, files=files)
    complete_setup(registry, "shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    workflow = Workflow(
        requests, registry, tmp_path / "projects",
        planning=MockPlanningModel(),
        execution=execution or MockExecutionModel(),
        review=review or MockReviewModel(),
    )
    remote = tmp_path / "remotes" / "shop-api"
    return registry, requests, workflow, remote


# --- the successful path ---------------------------------------------------

def test_a_request_is_committed_tested_reviewed_merged_and_pushed(tmp_path):
    _, requests, workflow, remote = make_env(tmp_path, test_command="test -f feature_refund.md")
    original = _remote_head(remote)
    req = requests.create("shop-api", "feature", "Add good.txt")

    result = workflow.run(req["id"])

    assert result["status"] == "awaiting_approval"
    assert result["branch"] == f"caliper/{req['id']}"
    assert result["base_commit"] == original
    assert result["commit_sha"] and result["diff_hash"]
    worktree = Path(result["worktree"])
    assert worktree.is_dir()
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == f"caliper/{req['id']}"
    assert (worktree / "feature_refund.md").is_file()

    # Nothing reached the default branch before the human approved.
    assert _remote_head(remote) == original
    assert _remote_file(remote, "feature_refund.md") is None

    reviewed_commit = result["commit_sha"]
    merged = workflow.approve_merge(req["id"])

    assert merged["status"] == "merged"
    assert merged["merge_commit"]
    assert merged["merge_commit"] != reviewed_commit
    assert _remote_head(remote) == merged["merge_commit"]
    assert "[mock change for req-1]" in _remote_file(remote, "feature_refund.md")
    # The merge commit has both parents: the old default and the reviewed commit.
    parents = _git(remote, "rev-list", "--parents", "-n", "1", "main").stdout.split()
    assert reviewed_commit in parents
    assert not worktree.exists()
    assert not (tmp_path / "work" / "shop-api" / req["id"]).exists()

    artifact = workflow.artifact_dir(req["id"]) / "merge-commit.txt"
    assert merged["merge_commit"] in artifact.read_text()


def test_review_sees_the_authoritative_git_diff_and_the_test_result(tmp_path):
    review = RecordingReview()
    _, requests, workflow, _ = make_env(tmp_path, review=review, test_command="test -f feature_refund.md")
    req = requests.create("shop-api", "feature", "Add good.txt")

    result = workflow.run(req["id"])

    assert result["status"] == "awaiting_approval"
    assert len(review.seen) == 1
    seen = review.seen[0]
    # The model only returned "--- /dev/null"; the reviewer must see the Git diff,
    # including Git's own headers, not the model's text.
    assert seen["diff"].startswith("diff --git a/feature_refund.md b/feature_refund.md")
    assert "new file mode" in seen["diff"]
    assert "index " in seen["diff"]
    assert seen["tests"] is not None
    artifact = workflow.artifact_dir(req["id"]) / "diff.patch"
    assert seen["diff"] == artifact.read_text()


def test_approval_requires_a_stored_passing_review(tmp_path):
    _, requests, workflow, remote = make_env(tmp_path)
    req = requests.create("shop-api", "feature", "Add refunds")
    workflow.run(req["id"])
    before = _remote_head(remote)

    (workflow.artifact_dir(req["id"]) / "review.json").unlink()

    with pytest.raises(WorkflowError, match="no review"):
        workflow.approve_merge(req["id"])
    assert requests.get(req["id"])["status"] == "awaiting_approval"
    assert _remote_head(remote) == before


def test_the_request_page_shows_test_output_and_git_evidence(tmp_path):
    registry, requests, workflow, _ = make_env(tmp_path)
    client = TestClient(create_app(registry=registry, requests=requests, workflow=workflow))
    client.post("/projects/shop-api/requests/new",
                data={"kind": "feature", "text": "Add refund endpoint", "extra": ""})

    run = client.post("/requests/req-1/run", follow_redirects=True)

    assert run.status_code == 200
    assert "The project's test command passed in the request worktree." in run.text
    assert "Git evidence" in run.text
    assert "caliper/req-1" in run.text
    reviewed = requests.get("req-1")["commit_sha"]
    assert reviewed in run.text

    approve = client.post("/requests/req-1/approve", follow_redirects=True)

    assert approve.status_code == 200
    assert "Merge commit" in approve.text
    assert requests.get("req-1")["merge_commit"] in approve.text


# --- unsafe changes --------------------------------------------------------

BAD_DIFFS = {
    "traversal": (
        "--- /dev/null\n+++ b/../escape.md\n@@ -0,0 +1 @@\n+escaped\n",
        "outside the project root",
    ),
    "absolute": (
        "--- /dev/null\n+++ /tmp/caliper-escape.md\n@@ -0,0 +1 @@\n+escaped\n",
        "outside the project",
    ),
    "malformed": ("I changed the file, it should work now.", "not a unified diff"),
    "empty": ("", "empty"),
    "binary": (
        "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-# project\n"
        "+# changed\nGIT binary patch\n",
        "binary",
    ),
    "does-not-apply": (
        "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-# not the real content\n+# changed\n",
        "does not apply",
    ),
}


@pytest.mark.parametrize("case", sorted(BAD_DIFFS))
def test_an_unsafe_change_never_reaches_the_project(tmp_path, case):
    diff, reason = BAD_DIFFS[case]
    execution = ScriptedExecution([diff])
    _, requests, workflow, remote = make_env(tmp_path, execution=execution)
    original = _remote_head(remote)
    req = requests.create("shop-api", "feature", "Add refunds")

    result = workflow.run(req["id"])

    assert result["status"] == "failed"
    assert reason in result["failure_reason"]
    assert execution.calls == MAX_LOOPS
    # The clone's default branch and the remote are untouched.
    assert _remote_head(remote) == original
    clone = tmp_path / "projects" / "shop-api" / "repo"
    assert (clone / "README.md").read_text(encoding="utf-8") == "# project\n"
    # The request worktree is clean and still at its base commit.
    assert workflow.workspace.status(result["worktree"]) == ""
    assert workflow.workspace.head(result["worktree"]) == result["base_commit"]
    assert not (tmp_path / "escape.md").exists()
    assert not Path("/tmp/caliper-escape.md").exists()


# --- tests are a gate ------------------------------------------------------

def test_a_failing_test_is_retried_then_passes_without_duplicating_the_change(tmp_path):
    execution = ScriptedExecution([
        _new_file_patch("bad.txt", "bad"),
        _new_file_patch("good.txt", "good"),
    ])
    _, requests, workflow, remote = make_env(
        tmp_path, execution=execution, test_command="test -f good.txt"
    )
    original = _remote_head(remote)
    req = requests.create("shop-api", "feature", "Add good.txt")

    result = workflow.run(req["id"])

    assert result["status"] == "awaiting_approval"
    assert execution.calls == 2
    assert execution.feedback[0] is None
    assert "test_failure" in execution.feedback[1]
    diff = (workflow.artifact_dir(req["id"]) / "diff.patch").read_text()
    assert "good.txt" in diff
    assert "bad.txt" not in diff
    worktree = Path(result["worktree"])
    assert (worktree / "good.txt").is_file()
    assert not (worktree / "bad.txt").exists()
    assert _remote_head(remote) == original


def test_a_change_cannot_weaken_its_own_test_gate(tmp_path):
    execution = ScriptedExecution([(
        "--- a/.caliper/manifest.yaml\n+++ b/.caliper/manifest.yaml\n"
        "@@ -1,2 +1,2 @@\n"
        " id: shop-api\n"
        '-test_command: "test -f good.txt"\n'
        '+test_command: "true"\n'
    )])
    _, requests, workflow, _ = make_env(
        tmp_path, execution=execution, test_command="test -f good.txt"
    )
    req = requests.create("shop-api", "feature", "Change the test command")

    result = workflow.run(req["id"])

    assert result["status"] == "failed"
    assert "Tests failed after 3 attempts" in result["failure_reason"]
    assert execution.calls == MAX_LOOPS


def test_a_project_without_a_test_command_blocks_the_request(tmp_path):
    _, requests, workflow, _ = make_env(tmp_path)
    # Remove the command from the repository the request would run against.
    clone = tmp_path / "projects" / "shop-api" / "repo"
    # Create the clone first, then change the remote content it will sync.
    workflow.workspace.prepare("shop-api", "req-warm")
    workflow.workspace.discard("shop-api", "req-warm")
    repo_manifest = clone / ".caliper" / "manifest.yaml"
    repo_manifest.write_text("id: shop-api\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "-c", "user.name=Caliper", "-c", "user.email=caliper@localhost", "commit", "-qm", "no tests")
    _git(clone, "push", "origin", "HEAD:refs/heads/main")

    req = requests.create("shop-api", "feature", "Add refunds")
    with pytest.raises(WorkflowError, match="no test_command"):
        workflow.run(req["id"])

    stored = requests.get(req["id"])
    assert stored["status"] == "failed"
    assert "no test_command" in stored["failure_reason"]


# --- approval never authorizes an unreviewed change ------------------------

def test_a_rejected_review_cannot_be_merged(tmp_path):
    _, requests, workflow, remote = make_env(tmp_path, review=RejectingReview())
    original = _remote_head(remote)
    req = requests.create("shop-api", "bugfix", "Fix rounding")

    result = workflow.run(req["id"])

    assert result["status"] == "failed"
    with pytest.raises(WorkflowError, match="expected 'awaiting_approval'"):
        workflow.approve_merge(req["id"])
    assert _remote_head(remote) == original


def test_a_branch_that_changed_after_review_is_refused(tmp_path):
    _, requests, workflow, remote = make_env(tmp_path)
    original = _remote_head(remote)
    req = requests.create("shop-api", "feature", "Add refunds")
    result = workflow.run(req["id"])

    worktree = Path(result["worktree"])
    (worktree / "sneaky.txt").write_text("not reviewed\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "-c", "user.name=Caliper", "-c", "user.email=caliper@localhost",
         "commit", "-qm", "sneaky")

    with pytest.raises(WorkflowError, match="changed after review"):
        workflow.approve_merge(req["id"])
    assert requests.get(req["id"])["status"] == "failed"
    assert _remote_head(remote) == original


def test_a_diff_hash_that_no_longer_matches_is_refused(tmp_path):
    _, requests, workflow, remote = make_env(tmp_path)
    original = _remote_head(remote)
    req = requests.create("shop-api", "feature", "Add refunds")
    workflow.run(req["id"])
    requests.update(req["id"], diff_hash="0" * 64)

    with pytest.raises(WorkflowError, match="no longer matches"):
        workflow.approve_merge(req["id"])
    assert requests.get(req["id"])["status"] == "failed"
    assert _remote_head(remote) == original


# --- merge and push failures ----------------------------------------------

def test_a_merge_conflict_fails_loudly_and_pushes_nothing(tmp_path):
    execution = ScriptedExecution([(
        "--- a/conflict.txt\n+++ b/conflict.txt\n@@ -1 +1 @@\n-base\n+from the request branch\n"
    )])
    _, requests, workflow, remote = make_env(
        tmp_path, execution=execution, files={"conflict.txt": "base\n"}
    )
    original = _remote_head(remote)
    req = requests.create("shop-api", "feature", "Change conflict.txt")
    workflow.run(req["id"])

    # Another change reaches the default branch after the request branch was cut.
    work = tmp_path / "remotes" / "shop-api-work"
    (work / "conflict.txt").write_text("from the default branch\n", encoding="utf-8")
    _git(work, "add", "conflict.txt")
    _git(work, "-c", "user.name=Human", "-c", "user.email=human@example.com",
         "commit", "-qm", "default branch moved")
    assert _git(work, "push", "origin", "main").returncode == 0
    moved = _remote_head(remote)
    assert moved != original

    with pytest.raises(WorkflowError, match="conflicts with the default branch"):
        workflow.approve_merge(req["id"])

    assert requests.get(req["id"])["status"] == "failed"
    assert _remote_head(remote) == moved
    assert _remote_file(remote, "conflict.txt") == "from the default branch\n"
    assert not (workflow.artifact_dir(req["id"]) / "merge-commit.txt").exists()


def test_a_rejected_push_fails_and_leaves_the_remote_unchanged(tmp_path):
    _, requests, workflow, remote = make_env(tmp_path)
    req = requests.create("shop-api", "feature", "Add refunds")
    workflow.run(req["id"])
    before = _remote_head(remote)

    hook = remote / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'push rejected by policy' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    with pytest.raises(WorkflowError, match="could not be pushed"):
        workflow.approve_merge(req["id"])

    assert requests.get(req["id"])["status"] == "failed"
    assert _remote_head(remote) == before
    merge_commit = workflow.artifact_dir(req["id"]) / "merge-commit.txt"
    assert not merge_commit.exists()


# --- execution adapters that edit the worktree directly -------------------

def test_the_opencode_execution_edits_the_worktree_and_returns_no_diff(tmp_path):
    seen = {}

    def runner(client, prompt):
        seen["cwd"] = client.cwd
        seen["prompt"] = prompt
        (Path(client.cwd) / "good.txt").write_text("written by opencode\n", encoding="utf-8")
        return json.dumps({"type": "text", "part": {"text": "done"}})

    client = OpenCodeClient(model="fake/model", runner=runner)
    execution = OpenCodeExecutionModel(client)
    _, requests, workflow, remote = make_env(
        tmp_path, execution=execution, test_command="test -f good.txt"
    )
    original = _remote_head(remote)
    req = requests.create("shop-api", "feature", "Add good.txt")

    result = workflow.run(req["id"])

    assert result["status"] == "awaiting_approval"
    assert seen["cwd"] == result["worktree"]
    assert "Edit the files in the current working directory" in seen["prompt"]
    assert "Do not print a diff" in seen["prompt"]
    assert "Produce code changes" not in seen["prompt"]
    assert "good.txt" in (workflow.artifact_dir(req["id"]) / "diff.patch").read_text()
    # The model never returned a patch; Caliper computed it from Git.
    assert (Path(result["worktree"]) / "good.txt").read_text() == "written by opencode\n"
    assert _remote_head(remote) == original


def test_the_http_adapters_run_the_full_loop_over_real_http(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    attach_real_repo(tmp_path, registry, "shop-api", test_command="test -f good.txt")
    complete_setup(registry, "shop-api")
    rule_ids = registry.approved_rule_ids("shop-api")
    diff = _new_file_patch("good.txt", "from http")
    prompts = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length))
            prompt = payload["messages"][0]["content"]
            prompts.append(prompt)
            if "Planning Model" in prompt:
                content = "# Plan\n\n## Rules relied on\n" + ", ".join(rule_ids) + "\n"
            elif "Execution Model" in prompt:
                content = diff
            elif "Review Model" in prompt:
                content = json.dumps({"verdicts": {
                    rid: {"status": "pass", "reason": "looks correct"} for rid in rule_ids
                }})
            else:
                content = "OK"
            body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        config = {
            role: {"provider": "http", "base_url": base_url, "model": f"{role}-model"}
            for role in ROLE_ORDER
        }
        adapters = make_http_adapters(config)
        requests = RequestStore(tmp_path / "state.sqlite3")
        workflow = Workflow(
            requests, registry, tmp_path / "projects",
            planning=adapters["planning"], execution=adapters["execution"],
            review=adapters["review"],
        )
        req = requests.create("shop-api", "feature", "Add good.txt")

        result = workflow.run(req["id"])
        merged = workflow.approve_merge(req["id"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result["status"] == "awaiting_approval"
    assert merged["status"] == "merged"
    remote = tmp_path / "remotes" / "shop-api"
    assert _remote_file(remote, "good.txt") == "from http\n"
    assert any("Current project code" in prompt for prompt in prompts)
    review_prompt = next(prompt for prompt in prompts if "Review Model" in prompt)
    assert "Diff:\ndiff --git a/good.txt b/good.txt" in review_prompt
    assert "test -f good.txt" not in review_prompt  # the reviewer sees results, not the command
    assert "Test results from the project's test command" in review_prompt


# --- interruption and stale worktrees -------------------------------------

def test_deleting_a_request_removes_its_worktree_and_branch(tmp_path):
    registry, requests, workflow, _ = make_env(tmp_path)
    req = requests.create("shop-api", "feature", "Add refunds")
    result = workflow.run(req["id"])
    worktree = Path(result["worktree"])
    assert worktree.is_dir()
    clone = tmp_path / "projects" / "shop-api" / "repo"
    assert f"caliper/{req['id']}" in _git(clone, "branch", "--list").stdout

    client = TestClient(create_app(registry=registry, requests=requests, workflow=workflow))

    response = client.delete(f"/api/requests/{req['id']}")

    assert response.status_code == 200
    assert requests.get(req["id"]) is None
    assert not worktree.exists()
    assert f"caliper/{req['id']}" not in _git(clone, "branch", "--list").stdout


def test_a_restart_marks_in_flight_requests_interrupted(tmp_path):
    registry, requests, workflow, _ = make_env(tmp_path)
    req = requests.create("shop-api", "feature", "Add refunds")
    requests.update(req["id"], status="executing")

    with TestClient(create_app(registry=registry, requests=requests, workflow=workflow)):
        pass

    recovered = requests.get(req["id"])
    assert recovered["status"] == "interrupted"
    assert "restarted" in recovered["failure_reason"]


def test_a_stale_worktree_is_recreated_cleanly(tmp_path):
    _, requests, workflow, _ = make_env(tmp_path)
    req = requests.create("shop-api", "feature", "Add refunds")
    first = workflow.workspace.prepare("shop-api", req["id"])
    (Path(first["path"]) / "stale.txt").write_text("stale\n", encoding="utf-8")

    second = workflow.workspace.prepare("shop-api", req["id"])

    assert second["path"] == first["path"]
    assert second["base_commit"] == first["base_commit"]
    assert not (Path(second["path"]) / "stale.txt").exists()
    assert _git(Path(second["path"]), "status", "--porcelain").stdout.strip() == ""
    workflow.workspace.discard("shop-api", req["id"])
