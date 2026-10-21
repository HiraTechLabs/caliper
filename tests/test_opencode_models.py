import json

import pytest

from caliper.opencode_models import OpenCodeAdapterError, OpenCodeClient, redact


def _cli_lines(*payloads):
    return "\n".join(json.dumps(payload) for payload in payloads)


def _text_part(text):
    return {"type": "text", "part": {"id": "p1", "type": "text", "text": text}}


def _client(stdout="", **kwargs):
    stored = []

    def runner(client, content):
        stored.append((client.command("run", "--format", "json", "--model", client.model, content), content))
        return stdout

    return OpenCodeClient("opencode/test-model", runner=runner, **kwargs), stored


def _stub(tmp_path, script):
    stub = tmp_path / "stub-opencode"
    stub.write_text(script)
    stub.chmod(0o755)
    return str(stub)


def test_command_is_an_argument_array_never_a_shell_string():
    client, stored = _client(_cli_lines(_text_part("OK")))
    reply = client.post('prompt with "quotes", $HOME, `backticks` and\nnewlines')
    assert reply == "OK"
    argv, content = stored[0]
    assert argv == [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        "opencode/test-model",
        'prompt with "quotes", $HOME, `backticks` and\nnewlines',
    ]


def test_auto_approval_is_never_requested():
    client, stored = _client(_cli_lines(_text_part("OK")))
    client.post("p")
    argv, _ = stored[0]
    assert "--auto" not in argv


def test_text_is_collected_from_json_lines_in_order():
    client, _ = _client(
        _cli_lines(
            {"type": "step_start", "part": {"type": "step-start"}},
            _text_part("first"),
            _text_part("second"),
        )
    )
    assert client.post("p") == "first\nsecond"


def test_non_text_lines_are_ignored():
    client, _ = _client(_cli_lines({"type": "tool", "part": {"type": "tool"}}) + "\n" + _cli_lines(_text_part("OK")))
    assert client.post("p") == "OK"


def test_missing_executable_is_reported_plainly():
    client = OpenCodeClient("opencode/x", executable="/definitely/not/opencode", timeout=5)
    with pytest.raises(OpenCodeAdapterError, match="not on PATH"):
        client.post("p")


def test_script_that_works_returns_the_text(tmp_path):
    executable = _stub(tmp_path, "#!/bin/sh\necho '{\"type\":\"text\",\"part\":{\"type\":\"text\",\"text\":\"OK\"}}'\n")
    client = OpenCodeClient("opencode/x", executable=executable, timeout=10)
    assert client.post("p") == "OK"


def test_nonzero_exit_reports_the_tail_without_the_prompt(tmp_path):
    executable = _stub(tmp_path, "#!/bin/sh\necho 'connection refused' >&2\nexit 3\n")
    client = OpenCodeClient("opencode/x", executable=executable, timeout=10)
    with pytest.raises(OpenCodeAdapterError) as excinfo:
        client.post("the-secret-prompt-text")
    assert "exit code 3" in str(excinfo.value)
    assert "connection refused" in str(excinfo.value)
    assert "the-secret-prompt-text" not in str(excinfo.value)


def test_timeout_is_enforced(tmp_path):
    executable = _stub(tmp_path, "#!/bin/sh\nsleep 5\necho ok\n")
    client = OpenCodeClient("opencode/x", executable=executable, timeout=1)
    with pytest.raises(OpenCodeAdapterError, match="did not answer within"):
        client.post("p")


def test_empty_stdout_is_an_error(tmp_path):
    executable = _stub(tmp_path, "#!/bin/sh\nexit 0\n")
    client = OpenCodeClient("opencode/x", executable=executable, timeout=10)
    with pytest.raises(OpenCodeAdapterError, match="empty response"):
        client.post("p")


def test_malformed_output_with_no_text_part_is_an_error(tmp_path):
    executable = _stub(tmp_path, "#!/bin/sh\necho '{oops not json'\n")
    client = OpenCodeClient("opencode/x", executable=executable, timeout=10)
    with pytest.raises(OpenCodeAdapterError, match="unexpected output format"):
        client.post("p")


def test_errors_redact_token_strings(tmp_path):
    executable = _stub(tmp_path, "#!/bin/sh\necho 'fatal: token sk-12345678deadbeef rejected' >&2\nexit 2\n")
    client = OpenCodeClient("opencode/x", executable=executable, timeout=10)
    with pytest.raises(OpenCodeAdapterError) as excinfo:
        client.post("p")
    assert "sk-12345678deadbeef" not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)


def test_redact_masks_common_token_shapes():
    text = "sk-abcdef12345678 ghp_0123456789abcdefghijklmnop Bearer verysecret github_pat_0123456789abcdefghij0123"
    result = redact(text)
    assert "sk-abcdef" not in result
    assert "ghp_" not in result
    assert "verysecret" not in result
    assert "github_pat_" not in result
