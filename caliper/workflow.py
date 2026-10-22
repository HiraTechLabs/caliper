import json
import re
from pathlib import Path

from caliper.patch_safety import PatchError
from caliper.state import RESUMABLE_STATUSES
from caliper.workspace import RequestWorkspaceService, WorkspaceError

MAX_LOOPS = 3

RULE_ID_RE = re.compile(r"\b([A-Z]{3}-\d{3})\b")

ATTEMPT_ARTIFACTS = (
    "review.json",
    "review-error.txt",
    "test-output.txt",
    "test-status.txt",
    "failure.txt",
    "merge-commit.txt",
)


class WorkflowError(Exception):
    pass


class WorkflowInterrupted(WorkflowError):
    """The process is shutting down; the run stops at a phase boundary."""


class ReviewInvalid(WorkflowError):
    """The review output cannot be used: structure or rule coverage is wrong."""


def validate_review(review: dict, rule_ids: list[str]) -> None:
    if not isinstance(review, dict) or "verdicts" not in review:
        raise ReviewInvalid("Review output is invalid: no 'verdicts' key.")
    verdicts = review["verdicts"]
    if not verdicts or any("status" not in v or "reason" not in v for v in verdicts.values()):
        raise ReviewInvalid("Review output is invalid: entry without status/reason.")
    if any(v["status"] not in ("pass", "fail") for v in verdicts.values()):
        raise ReviewInvalid("Review output is invalid: status must be pass or fail.")
    expected = set(rule_ids)
    returned = set(verdicts)
    missing = sorted(expected - returned)
    if missing:
        raise ReviewInvalid(
            "Review output is invalid: no verdict for approved rule(s): " + ", ".join(missing) + "."
        )
    unknown = sorted(returned - expected)
    if unknown:
        raise ReviewInvalid(
            "Review output is invalid: verdict for non-approved rule(s): " + ", ".join(unknown) + "."
        )


def cited_rule_ids(text: str) -> list[str]:
    """Rule IDs the planner cites, in first-citation order, without repeats."""
    return list(dict.fromkeys(RULE_ID_RE.findall(text or "")))


def validate_plan(plan: str, rule_ids: list[str]) -> None:
    cited = cited_rule_ids(plan)
    if not cited:
        raise WorkflowError(
            "Planning output cites no principle rule IDs. A plan without rule IDs is invalid."
        )
    approved = set(rule_ids)
    unknown = [rid for rid in cited if rid not in approved]
    if unknown:
        raise WorkflowError(
            "Planning output cites rules that are not approved: " + ", ".join(unknown) + "."
        )


class DeploymentNotConfigured(WorkflowError):
    """No real deploy adapter is configured; Caliper must not fake a deploy."""


def failed_rules(review: dict) -> list[str]:
    return [rid for rid, v in review["verdicts"].items() if v["status"] == "fail"]


def _summarize(text: str, limit: int = 2000) -> str:
    text = " ".join((text or "").split())
    return text[-limit:] if len(text) > limit else text


class Workflow:
    def __init__(self, requests, registry, artifacts_root: Path, planning=None, execution=None,
                 review=None, deploy=None, docs=None, workspace=None, stop_event=None):
        self.requests = requests
        self.registry = registry
        self.artifacts_root = Path(artifacts_root)
        self.planning = planning
        self.execution = execution
        self.review = review
        self.deploy = deploy
        self.docs = docs
        self.workspace = workspace or RequestWorkspaceService(registry)
        self.stop_event = stop_event

    @property
    def deploy_configured(self) -> bool:
        return self.deploy is not None

    def missing_models(self) -> list[str]:
        labels = {"planning": "Planning", "execution": "Execution", "review": "Review"}
        return [labels[role] for role in ("planning", "execution", "review") if getattr(self, role) is None]

    @property
    def models_configured(self) -> bool:
        return not self.missing_models()

    def artifact_dir(self, req_id: str) -> Path:
        req = self.requests.get(req_id)
        if req is None:
            raise WorkflowError(f"Request '{req_id}' does not exist.")
        dir = self.artifacts_root / req["project_id"] / req_id
        return dir

    def _project_context(self, project_id: str) -> dict:
        project = self.registry.get_project(project_id)
        project["project_docs"] = (
            self.docs.read_cached(project_id) if self.docs is not None else {}
        )
        return project

    def _feedback(self, req: dict, review: dict | None) -> dict | None:
        """What Execution must address on a re-run: failed verdicts and the human note."""
        failed = {}
        if review is not None:
            failed = {rid: review["verdicts"][rid] for rid in failed_rules(review)}
        note = (req.get("review_note") or "").strip()
        if not failed and not note:
            return None
        return {"failed_rules": failed, "human_note": note}

    def _mark_failed(self, req_id: str, reason: str) -> dict:
        reason = _summarize(reason)
        dir = self.artifact_dir(req_id)
        dir.mkdir(parents=True, exist_ok=True)
        (dir / "failure.txt").write_text(reason + "\n", encoding="utf-8")
        return self.requests.update(req_id, status="failed", failure_reason=reason)

    def _check_stop(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise WorkflowInterrupted(
                "Caliper is shutting down; this run was interrupted and can be run again."
            )

    def _mark_interrupted(self, req_id: str) -> dict:
        dir = self.artifact_dir(req_id)
        dir.mkdir(parents=True, exist_ok=True)
        reason = "Caliper is shutting down; this run was interrupted and can be run again."
        (dir / "interrupted.txt").write_text(reason + "\n", encoding="utf-8")
        return self.requests.update(req_id, status="interrupted", failure_reason=reason)

    def _clear_attempt_artifacts(self, req_id: str) -> None:
        dir = self.artifact_dir(req_id)
        dir.mkdir(parents=True, exist_ok=True)
        for name in ATTEMPT_ARTIFACTS:
            (dir / name).unlink(missing_ok=True)

    def _workspace_info(self, req: dict) -> dict:
        required = ("branch", "worktree", "base_commit")
        missing = [name for name in required if not req.get(name)]
        if missing:
            raise WorkflowError(
                "This request has no request worktree ("
                + ", ".join(missing)
                + " missing). Run the request again."
            )
        return {
            "branch": req["branch"],
            "worktree": req["worktree"],
            "base_commit": req["base_commit"],
            "commit_sha": req.get("commit_sha"),
            "diff_hash": req.get("diff_hash"),
        }

    def _prepare(self, req: dict) -> tuple[dict, str]:
        info = self.workspace.prepare(req["project_id"], req["id"])
        self.requests.update(
            req["id"],
            branch=info["branch"],
            worktree=info["path"],
            base_commit=info["base_commit"],
            commit_sha=None,
            diff_hash=None,
            merge_commit=None,
            failure_reason=None,
        )
        test_command = self.workspace.test_command_at(info["path"], info["base_commit"])
        if not test_command:
            raise WorkflowError(
                "The project has no test_command in .caliper/manifest.yaml. "
                "Add the command that verifies the project's tests so Caliper can gate the change."
            )
        return info, test_command

    def _run_execution(self, req: dict, project: dict, plan: str, feedback: dict | None,
                       info: dict) -> None:
        """Run the configured Execution role against the reset worktree."""
        path = info["worktree"]
        self.workspace.reset(path, info["base_commit"])
        workspace_aware = callable(getattr(self.execution, "execute_in_workspace", None))
        if workspace_aware:
            self.execution.execute_in_workspace(req, project, plan, feedback, workspace=path)
            return
        exec_project = dict(project)
        exec_project["code_context"] = self.workspace.context(path, plan)
        diff = self.execution.execute(req, exec_project, plan, feedback)
        self.workspace.apply(diff, path)

    def _attempt(self, req: dict, project: dict, plan: str, feedback: dict | None,
                 info: dict, test_command: str) -> dict:
        """Apply one change, commit it, run the tests, and return the evidence."""
        req_id = req["id"]
        path = info["worktree"]
        base = info["base_commit"]
        self._check_stop()
        self._clear_attempt_artifacts(req_id)
        self.requests.update(req_id, status="executing", failure_reason=None, commit_sha=None, diff_hash=None)

        self._run_execution(req, project, plan, feedback, info)
        self._check_stop()
        diff = self.workspace.stage_and_diff(path, base)
        (self.artifact_dir(req_id) / "diff.patch").write_text(diff, encoding="utf-8")

        commit = self.workspace.commit(
            path, f"caliper: {req_id} {_summarize(req['text'], 72)}"
        )
        self._check_stop()
        passed, output = self.workspace.run_tests(path, test_command)
        self._check_stop()
        dir = self.artifact_dir(req_id)
        (dir / "test-output.txt").write_text(output + "\n", encoding="utf-8")
        (dir / "test-status.txt").write_text(("passed" if passed else "failed") + "\n", encoding="utf-8")

        self.workspace.clean_untracked(path)
        if passed:
            status = self.workspace.status(path)
            if status:
                raise WorkspaceError(
                    "The test command changed tracked files after the reviewed commit was created. "
                    "Caliper does not merge changes that were not reviewed. Fix the test command "
                    "so it does not rewrite tracked files."
                )
        return {"passed": passed, "output": output, "commit": commit, "diff": diff}

    def _review_once(self, req: dict, project: dict, plan: str, rule_ids: list[str],
                     diff: str, test_output: str) -> tuple[dict | None, str | None]:
        dir = self.artifact_dir(req["id"])
        self._check_stop()
        self.requests.update(req["id"], status="reviewing")
        review = None
        error = None
        try:
            review = self.review.review(req, project, plan, diff, rule_ids, tests=test_output)
            self._check_stop()
            validate_review(review, rule_ids)
        except ReviewInvalid as exc:
            error = str(exc)
        if error:
            (dir / "review-error.txt").write_text(error + "\n", encoding="utf-8")
        else:
            (dir / "review.json").write_text(json.dumps(review, indent=2), encoding="utf-8")
        return review, error

    def _run_loop(self, req: dict, project: dict, plan: str, rule_ids: list[str],
                  feedback: dict | None = None) -> dict:
        """Execute, test and review until a change passes or the loop limit is reached."""
        req_id = req["id"]
        while True:
            self._check_stop()
            current = self.requests.get(req_id)
            loops = current["loop_count"]
            info = self._workspace_info(current)
            test_command = self.workspace.test_command_at(info["worktree"], info["base_commit"])
            if not test_command:
                return self._mark_failed(
                    req_id,
                    "The project has no test_command in .caliper/manifest.yaml. "
                    "Add the command that verifies the project's tests so Caliper can gate the change.",
                )
            try:
                attempt = self._attempt(current, project, plan, feedback, info, test_command)
            except (WorkspaceError, PatchError) as exc:
                reason = str(exc)
                if loops + 1 < MAX_LOOPS:
                    current = self.requests.update(req_id, loop_count=loops + 1)
                    feedback = self._feedback(current, None) or {}
                    feedback["invalid_patch"] = reason
                    continue
                return self._mark_failed(req_id, f"The change could not be applied safely: {reason}")

            if not attempt["passed"]:
                if loops + 1 < MAX_LOOPS:
                    current = self.requests.update(req_id, loop_count=loops + 1)
                    feedback = self._feedback(current, None) or {}
                    feedback["test_failure"] = attempt["output"]
                    continue
                return self._mark_failed(
                    req_id,
                    f"Tests failed after {MAX_LOOPS} attempts: {_summarize(attempt['output'])}",
                )

            review, error = self._review_once(
                current, project, plan, rule_ids, attempt["diff"], attempt["output"]
            )
            failed = failed_rules(review) if review is not None else []
            if error or failed:
                if loops + 1 < MAX_LOOPS:
                    current = self.requests.update(req_id, loop_count=loops + 1)
                    feedback = self._feedback(current, review) or {}
                    if error:
                        feedback["invalid_review"] = error
                    continue
                if error:
                    return self._mark_failed(req_id, f"The review could not be used after {MAX_LOOPS} attempts: {error}")
                return self._mark_failed(
                    req_id,
                    f"Review failed after {MAX_LOOPS} attempts: {', '.join(failed)}.",
                )

            diff_hash = self.workspace.diff_hash(attempt["diff"])
            return self.requests.update(
                req_id,
                status="awaiting_approval",
                commit_sha=attempt["commit"],
                diff_hash=diff_hash,
                failure_reason=None,
            )

    def run(self, req_id: str) -> dict:
        self._check_stop()
        missing = self.missing_models()
        if missing:
            raise WorkflowError("Models are not configured: " + ", ".join(missing) + ". Open Settings > Models.")
        req = self.requests.get(req_id)
        if req is None:
            raise WorkflowError(f"Request '{req_id}' does not exist.")
        if req["status"] not in RESUMABLE_STATUSES:
            raise WorkflowError(
                f"Request {req_id} is '{req['status']}', expected 'submitted' or 'interrupted'."
            )
        rule_ids = self.registry.approved_rule_ids(req["project_id"])
        if not rule_ids:
            raise WorkflowError(
                "The project has no approved principle rules. Approve the principles first."
            )
        dir = self.artifact_dir(req_id)
        dir.mkdir(parents=True, exist_ok=True)
        try:
            info, _test_command = self._prepare(req)
            self._check_stop()
            project = self._project_context(req["project_id"])
            self.requests.update(req_id, status="planning")
            plan = self.planning.plan(req, project, rule_ids)
            (dir / "plan.md").write_text(plan, encoding="utf-8")
            validate_plan(plan, rule_ids)
            return self._run_loop(req, project, plan, rule_ids)
        except WorkflowInterrupted:
            self._mark_interrupted(req_id)
            raise
        except Exception as exc:
            self._mark_failed(req_id, str(exc) or exc.__class__.__name__)
            raise

    def _expect(self, req_id: str, *statuses: str) -> dict:
        req = self.requests.get(req_id)
        if req is None:
            raise WorkflowError(f"Request '{req_id}' does not exist.")
        if req["status"] not in statuses:
            expected = " or ".join(statuses)
            raise WorkflowError(f"Request {req_id} is '{req['status']}', expected '{expected}'.")
        return req

    def request_changes(self, req_id: str, note: str) -> dict:
        req = self._expect(req_id, "awaiting_approval")
        if not note or not note.strip():
            raise WorkflowError("A note is required when requesting changes.")
        note = note.strip()
        req = self.requests.update(
            req_id, status="executing", review_note=note, commit_sha=None,
            diff_hash=None, failure_reason=None,
        )
        dir = self.artifact_dir(req_id)
        with (dir / "human-feedback.log").open("a", encoding="utf-8") as f:
            f.write(note + "\n")
        plan = (dir / "plan.md").read_text(encoding="utf-8")
        try:
            project = self._project_context(req["project_id"])
            rule_ids = self.registry.approved_rule_ids(req["project_id"])
            return self._run_loop(req, project, plan, rule_ids, self._feedback(req, None))
        except WorkflowInterrupted:
            self._mark_interrupted(req_id)
            raise
        except Exception as exc:
            self._mark_failed(req_id, str(exc) or exc.__class__.__name__)
            raise

    def _require_passing_review(self, req: dict) -> None:
        review_path = self.artifact_dir(req["id"]) / "review.json"
        if not review_path.is_file():
            raise WorkflowError("This request has no review. It cannot be merged.")
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"The stored review cannot be read: {exc}")
        rule_ids = self.registry.approved_rule_ids(req["project_id"])
        validate_review(review, rule_ids)
        failed = failed_rules(review)
        if failed:
            raise WorkflowError("The reviewed change has failed rules and cannot be merged: " + ", ".join(failed) + ".")

    def approve_merge(self, req_id: str) -> dict:
        req = self._expect(req_id, "awaiting_approval")
        self._require_passing_review(req)
        info = self._workspace_info(req)
        if not info["commit_sha"] or not info["diff_hash"]:
            raise WorkflowError("This request has no reviewed commit. Run the request again.")
        try:
            self.workspace.verify_reviewed(
                info["worktree"], info["base_commit"], info["commit_sha"], info["diff_hash"]
            )
            result = self.workspace.merge(req["project_id"], info["branch"], info["commit_sha"])
        except (WorkspaceError, PatchError) as exc:
            self._mark_failed(req_id, str(exc))
            raise WorkflowError(str(exc))
        dir = self.artifact_dir(req_id)
        (dir / "merge-commit.txt").write_text(
            f"merge {result['merge_commit']}\n"
            f"into {result['default_branch']}\n"
            f"branch {info['branch']}\n",
            encoding="utf-8",
        )
        self.requests.update(
            req_id, status="merged", merge_commit=result["merge_commit"], failure_reason=None
        )
        try:
            self.workspace.discard(req["project_id"], req_id)
        except WorkspaceError:
            # The merge and push already happened; a leftover worktree is recoverable state.
            pass
        if not self.deploy_configured:
            return self.requests.get(req_id)
        return self.deploy_dev(req_id)

    def discard_workspace(self, req_id: str) -> None:
        """Remove the request worktree/branch (request deletion or explicit cleanup).

        Called even when the stored branch/worktree fields are empty, so a crash
        between ``git worktree add`` and the state update cannot leave an
        orphaned branch behind. ``discard`` is idempotent and a no-op without a
        local clone.
        """
        req = self.requests.get(req_id)
        if req is None or not req.get("project_id"):
            return
        try:
            self.workspace.discard(req["project_id"], req_id)
        except WorkspaceError as exc:
            raise WorkflowError(str(exc))
        self.requests.update(
            req_id, branch=None, worktree=None, base_commit=None,
            commit_sha=None, diff_hash=None,
        )

    def _require_deploy(self) -> None:
        if not self.deploy_configured:
            raise DeploymentNotConfigured(
                "Deployment is not configured. The change is merged, but Dev and Prod deploys "
                "stay blocked until a deploy adapter is set up."
            )

    def deploy_dev(self, req_id: str) -> dict:
        self._require_deploy()
        req = self._expect(req_id, "merged", "deployed_dev")
        project = self.registry.get_project(req["project_id"])
        record = self.deploy.deploy(project, "dev", req)
        (self.artifact_dir(req_id) / "deploy-dev.log").write_text(record + "\n", encoding="utf-8")
        return self.requests.update(req_id, status="deployed_dev")

    def approve_prod(self, req_id: str) -> dict:
        self._require_deploy()
        req = self._expect(req_id, "deployed_dev")
        project = self.registry.get_project(req["project_id"])
        record = self.deploy.deploy(project, "prod", req)
        (self.artifact_dir(req_id) / "deploy-prod.log").write_text(record + "\n", encoding="utf-8")
        return self.requests.update(req_id, status="live")
