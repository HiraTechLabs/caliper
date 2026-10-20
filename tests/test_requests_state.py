import pytest

from caliper.state import RequestStore


@pytest.fixture()
def store(tmp_path):
    return RequestStore(tmp_path / "state.sqlite3")


def test_create_returns_submitted_request(store):
    req = store.create("shop-api", "feature", "Add refund endpoint")
    assert req["id"] == "req-1"
    assert req["status"] == "submitted"
    assert req["kind"] == "feature"


def test_ids_increment(store):
    store.create("a", "feature", "x")
    req = store.create("b", "bugfix", "y")
    assert req["id"] == "req-2"


def test_empty_text_rejected(store):
    with pytest.raises(ValueError, match="text"):
        store.create("a", "feature", "   ")


def test_invalid_kind_rejected(store):
    with pytest.raises(ValueError, match="feature"):
        store.create("a", "improvement", "x")


def test_extra_stored(store):
    req = store.create("a", "bugfix", "crashes", extra="TypeError: ...")
    assert req["extra"] == "TypeError: ..."


def test_list_filtering_and_order(store):
    store.create("a", "feature", "one")
    store.create("a", "bugfix", "two")
    store.create("b", "feature", "other")
    assert [r["id"] for r in store.list_for_project("a")] == ["req-2", "req-1"]
    assert len(store.list_all()) == 3


def test_update_allowed_fields_only(store):
    req = store.create("a", "feature", "x")
    updated = store.update(req["id"], status="planning", loop_count=1)
    assert updated["status"] == "planning"
    assert updated["loop_count"] == 1
    with pytest.raises(ValueError):
        store.update(req["id"], text="nope")
    with pytest.raises(ValueError):
        store.update("req-99", status="planning")


def test_missing_request_returns_none(store):
    assert store.get("req-42") is None
