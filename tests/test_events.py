import json

import pytest
from fastapi.testclient import TestClient

from caliper.events import EventBus
from caliper.main import create_app
from caliper.registry import ProjectRegistry
from caliper.state import RequestStore
from tests.mock_models import mock_workflow
from tests.support import complete_setup


def test_event_bus_fans_out_and_drops_slow_subscribers():
    bus = EventBus()
    first = bus.subscribe()
    second = bus.subscribe()
    bus.publish({"change": "updated", "id": "req-1"})
    assert first.get_nowait()["id"] == "req-1"
    assert second.get_nowait()["id"] == "req-1"
    bus.unsubscribe(second)
    assert bus.subscriber_count() == 1


def test_request_store_notifies_on_create_and_update(tmp_path):
    store = RequestStore(tmp_path / "state.sqlite3")
    changes = []
    store.on_change = lambda change, req: changes.append((change, req["id"], req["status"]))
    req = store.create(project_id="shop-api", kind="feature", text="Add refunds")
    store.update(req["id"], status="planning")
    assert changes == [("created", "req-1", "submitted"), ("updated", "req-1", "planning")]


@pytest.fixture()
def client(tmp_path):
    registry = ProjectRegistry(tmp_path / "projects")
    registry.create_project(name="Shop API", repo_url="https://github.com/example/shop-api")
    complete_setup(registry, "shop-api")
    requests = RequestStore(tmp_path / "state.sqlite3")
    workflow = mock_workflow(requests, registry, tmp_path / "projects")
    return TestClient(create_app(registry=registry, requests=requests, workflow=workflow))


def test_sse_snapshot_carries_real_request_state(client):
    client.post("/projects/shop-api/requests/new", data={"kind": "feature", "text": "Add refunds", "extra": ""})
    response = client.get("/api/events?once=1")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: snapshot" in response.text
    data = json.loads(response.text.split("data: ", 1)[1].strip())
    assert data["requests"][0]["id"] == "req-1"
    assert data["requests"][0]["status"] == "submitted"


def test_sse_request_stream_includes_artifacts(client):
    client.post("/projects/shop-api/requests/new", data={"kind": "feature", "text": "Add refunds", "extra": ""})
    client.post("/requests/req-1/run")
    response = client.get("/api/events?request=req-1&once=1")
    data = json.loads(response.text.split("data: ", 1)[1].strip())
    request = data["requests"][0]
    assert request["status"] == "awaiting_approval"
    assert request["plan"]
    assert request["diff"]
    assert request["review"]
    assert request["fail_count"] == 0


def test_screens_expose_motion_hooks(client):
    client.post("/projects/shop-api/requests/new", data={"kind": "feature", "text": "Add refunds", "extra": ""})
    dashboard = client.get("/")
    assert 'data-factory-floor' in dashboard.text
    assert 'data-floor-toggle' in dashboard.text
    assert 'data-request-list="needs"' in dashboard.text
    assert '/static/app.js' in dashboard.text

    detail = client.get("/requests/req-1")
    assert 'data-request-root' in detail.text
    assert 'data-factory-floor' in detail.text
    assert 'data-status-badge' in detail.text
    assert 'data-review-section' in detail.text
    assert 'data-action-area' in detail.text
