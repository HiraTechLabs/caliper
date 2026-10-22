"""Health endpoints, startup recovery and clean shutdown (PLAN_P1 1.3)."""

import base64
import threading

import pytest
from fastapi.testclient import TestClient

from caliper.config import CaliperConfig, ConfigError
from caliper.lifecycle import Lifecycle, ShuttingDown
from caliper.main import create_app
from caliper.secrets import SecretStore
from caliper.state import RequestStore
from caliper.workflow import Workflow, WorkflowInterrupted

KEY = bytes(range(32, 64))


def _config(tmp_path) -> CaliperConfig:
    data = tmp_path / "data"
    return CaliperConfig(
        data_dir=data,
        db_path=data / "caliper.db",
        projects_root=data / "projects",
        work_root=data / "work",
        secrets_dir=data / "secrets",
        models_config=data / "models.yaml",
        runtime_dir=tmp_path / "run",
        shutdown_grace_seconds=1,
    )


def _app(tmp_path, key=KEY, requests=None):
    config = _config(tmp_path)
    store = SecretStore(config.secret_store_path, key)
    return create_app(config=config, secret_store=store, requests=requests), config


def test_live_and_ready_return_200_after_startup(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json()["check"] == "live"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["checks"]["database"]["ok"] is True
    assert ready.json()["checks"]["data_dir"]["ok"] is True
    assert ready.json()["checks"]["secret_store"]["ok"] is True


def test_ready_is_503_before_startup_checks_run(tmp_path):
    app, _ = _app(tmp_path)
    # No context manager: the lifespan (and the startup checks) has not run.
    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["startup"]["ok"] is False


def test_ready_is_503_when_the_secret_store_is_locked(tmp_path):
    app, _ = _app(tmp_path, key=None)
    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["secret_store"]["ok"] is False
    assert "locked" in response.json()["checks"]["secret_store"]["detail"]


def test_startup_refuses_a_locked_store(tmp_path, monkeypatch):
    monkeypatch.delenv("CALIPER_MASTER_KEY", raising=False)
    monkeypatch.delenv("CALIPER_MASTER_KEY_FILE", raising=False)
    app, _ = _app(tmp_path, key=None)

    with pytest.raises(ConfigError, match="init-secrets"):
        with TestClient(app):
            pass


def test_startup_refuses_a_well_formed_key_that_does_not_match_the_store(tmp_path, monkeypatch):
    config = _config(tmp_path)
    first = SecretStore(config.secret_store_path, KEY)
    first.put("model/planning", "api_key", "sk-existing")
    first.close()
    other = bytes(reversed(KEY))
    monkeypatch.setenv("CALIPER_MASTER_KEY", base64.urlsafe_b64encode(other).decode("ascii"))
    store = SecretStore(config.secret_store_path, other)
    app = create_app(config=config, secret_store=store)

    with pytest.raises(ConfigError, match="does not match"):
        with TestClient(app):
            pass


def test_startup_marks_in_flight_runs_interrupted_and_resumable(tmp_path):
    config = _config(tmp_path)
    requests = RequestStore(config.db_path)
    req = requests.create("shop-api", "feature", "Add refunds")
    requests.update(req["id"], status="executing", branch="caliper/req-1", base_commit="abc")
    app, _ = _app(tmp_path, requests=requests)

    with TestClient(app):
        pass

    recovered = requests.get(req["id"])
    assert recovered["status"] == "interrupted"
    assert "restarted" in recovered["failure_reason"]
    # The recorded base commit is kept so the run can resume.
    assert recovered["base_commit"] == "abc"


def test_shutdown_stops_accepting_runs_and_marks_active_ones_interrupted(tmp_path):
    config = _config(tmp_path)
    requests = RequestStore(config.db_path)
    req = requests.create("shop-api", "feature", "Add refunds")
    requests.update(req["id"], status="executing")
    app, _ = _app(tmp_path, requests=requests)
    lifecycle = app.state.lifecycle

    with TestClient(app):
        lifecycle.begin(req["id"])
        assert lifecycle.accepting_runs is True

    assert lifecycle.accepting_runs is False
    assert lifecycle.active_runs == []
    with pytest.raises(ShuttingDown):
        lifecycle.begin("req-2")
    assert requests.get(req["id"])["status"] == "interrupted"


def test_a_stop_event_interrupts_the_workflow_at_a_phase_boundary(tmp_path):
    requests = RequestStore(tmp_path / "state.sqlite3")
    req = requests.create("shop-api", "feature", "Add refunds")
    stop = threading.Event()

    class WorkspaceThatGetsInterrupted:
        def prepare(self, project_id, req_id):
            stop.set()
            return {"branch": f"caliper/{req_id}", "path": str(tmp_path), "base_commit": "abc"}

        def test_command_at(self, path, commit):
            return "true"

    class RegistryWithRules:
        def approved_rule_ids(self, project_id):
            return ["SEC-001"]

    workflow = Workflow(
        requests, RegistryWithRules(), tmp_path / "projects",
        planning=object(), execution=object(), review=object(),
        workspace=WorkspaceThatGetsInterrupted(), stop_event=stop,
    )

    with pytest.raises(WorkflowInterrupted):
        workflow.run(req["id"])

    assert requests.get(req["id"])["status"] == "interrupted"


def test_lifecycle_without_active_runs_marks_all_in_flight_requests(tmp_path):
    requests = RequestStore(tmp_path / "state.sqlite3")
    req = requests.create("shop-api", "feature", "Add refunds")
    requests.update(req["id"], status="reviewing")
    lifecycle = Lifecycle(requests)

    lifecycle.shutdown()

    assert lifecycle.accepting_runs is False
    assert requests.get(req["id"])["status"] == "interrupted"
