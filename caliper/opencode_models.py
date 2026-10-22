"""OpenCode role client.

Runs one prompt through the installed OpenCode CLI in JSON mode:

    opencode run --format json --model <provider/model> <prompt>

The CLI keeps its own credentials and model registry (``opencode models``);
Caliper stores none of that. The prompt is passed as a single argv element, so
no shell is involved anywhere. Output is JSON lines where text parts carry
``part.text``; anything else (tool calls, steps) is ignored. Never pass
``--auto``: headless runs must not be able to approve an action on their own.
"""

import json
import re
import shutil
import subprocess

DEFAULT_TIMEOUT = 600
MAX_ERROR_OUTPUT = 1200

# Token-like strings are masked before anything of a failure is shown.
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"Bearer\s+\S+"),
)


def redact(text: str | None) -> str:
    """Mask anything that looks like an API token in operator-facing text."""
    result = str(text or "")
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[redacted]", result)
    return result


class OpenCodeAdapterError(Exception):
    pass


class OpenCodeClient:
    """Duck-compatible with caliper.http_models.ChatClient (a ``post`` method)."""

    def __init__(self, model: str, executable: str = "opencode", timeout: float = DEFAULT_TIMEOUT, cwd=None, runner=None):
        self.model = model
        self.executable = executable
        self.timeout = timeout
        self.cwd = cwd
        self._runner = runner

    def post(self, content: str) -> str:
        if self._runner is not None:
            # The runner stands in for subprocess and returns the CLI stdout.
            raw = self._runner(self, content)
            reply = _text_from_json_lines(raw)
            if not reply.strip():
                raise OpenCodeAdapterError(
                    "OpenCode returned an empty response. Check `opencode models` for available model ids."
                )
            return reply
        argv = self.command("run", "--format", "json", "--model", self.model, content)
        self._check_executable()
        try:
            completed = subprocess.run(
                argv,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise OpenCodeAdapterError(
                f"OpenCode did not answer within {self.timeout:g} seconds. "
                "Raise the timeout in Settings > Models or check that the model is available."
            )
        except OSError as exc:
            raise OpenCodeAdapterError(f"OpenCode could not be started: {redact(exc)}")
        if completed.returncode != 0:
            raise OpenCodeAdapterError(self._failure(completed.stderr or completed.stdout, completed.returncode))
        reply = _text_from_json_lines(completed.stdout)
        if not reply.strip():
            raise OpenCodeAdapterError(
                "OpenCode returned an empty response. Check `opencode models` for available model ids."
            )
        return reply

    def command(self, *args: str) -> list[str]:
        """The argv array; one argument per element, never a shell string."""
        return [self.executable, *args]

    def _check_executable(self) -> None:
        if shutil.which(self.executable) is None:
            raise OpenCodeAdapterError(
                f"The executable '{self.executable}' is not on PATH. Install OpenCode and try again."
            )

    def _failure(self, output: str, returncode: int) -> str:
        detail = redact("\n".join(
            line.strip() for line in str(output).splitlines() if line.strip()
        ))[-MAX_ERROR_OUTPUT:]
        return f"OpenCode failed with exit code {returncode}. {detail or 'No error output.'}"


class OpenCodeExecutionModel:
    """Execution that edits the request worktree directly and returns no diff.

    Caliper computes the authoritative diff from Git after this call (D-047).
    The prompt explicitly forbids commits and paths outside the worktree.
    """

    def __init__(self, client: OpenCodeClient):
        self.client = client

    def execute(self, req: dict, project: dict, plan: str, feedback: dict | None = None) -> str:
        raise OpenCodeAdapterError(
            "The OpenCode Execution role edits the request worktree; it must be run with a workspace."
        )

    def execute_in_workspace(self, req: dict, project: dict, plan: str, feedback: dict | None,
                             workspace) -> None:
        from caliper.http_models import _docs_block

        feedback = feedback or {}
        failed = feedback.get("failed_rules") or {}
        note = str(feedback.get("human_note") or "").strip()
        parts = [
            "You are the Execution Model of a private software factory.\n"
            "You are working inside an isolated Git worktree for one request. "
            "Edit the files in the current working directory to implement the plan. "
            "Do not commit, do not push, and do not touch files outside the working directory.\n"
            f"Project: {project['id']}\n"
            + _docs_block(project)
            + f"Request: {req['text']}\nPlan:\n{plan}\n"
        ]
        if failed:
            parts.append(
                "The previous review failed these rules; fix every one of them:\n"
                + "\n".join(f"- {rid}: {v.get('reason', '')}" for rid, v in failed.items())
                + "\n"
            )
        if note:
            parts.append(f"The human asked for these changes:\n{note}\n")
        invalid = str(feedback.get("invalid_review") or "").strip()
        if invalid:
            parts.append(f"The previous review could not be used: {invalid}\n")
        patch_error = str(feedback.get("invalid_patch") or "").strip()
        if patch_error:
            parts.append(f"The previous change was rejected by Caliper's safety checks: {patch_error}\n")
        test_failure = str(feedback.get("test_failure") or "").strip()
        if test_failure:
            parts.append(
                "The project's tests failed for the previous change. Fix the code so they pass:\n"
                + test_failure[-8000:] + "\n"
            )
        parts.append(
            "Run the project's tests in this worktree if you can. "
            "Do not print a diff; Caliper computes it from your edits."
        )
        self.client.cwd = str(workspace)
        self.client.post("".join(parts))


def _text_from_json_lines(raw: str) -> str:
    """Every ``type: text`` line contributes its ``part.text``; parts join in order."""
    parts: list[str] = []
    parse_error = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            parse_error = parse_error or f"not valid JSON: {exc}"
            continue
        if isinstance(payload, dict) and payload.get("type") == "text":
            part = payload.get("part") or {}
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
    if not parts and parse_error:
        raise OpenCodeAdapterError(f"OpenCode returned an unexpected output format ({parse_error}).")
    return "\n".join(parts)
