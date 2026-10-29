import pytest

from tracemind.web.store import FIELDS, ReviewStore


def test_independent_locked_reviews_and_reveal(tmp_path):
    store = ReviewStore(tmp_path / "reviews.db")
    packet = {
        "id": "1",
        "case_id": "c",
        "checkpoint_id": "t",
        "public_history": "history",
        "response_a": "one",
        "response_b": "two",
        "mapping": {"a": "evolution", "b": "summary"},
        "visible_input": "hidden",
    }
    store.initialize({"alice": "password1", "bob": "password2"}, [packet])
    assert store.authenticate("alice", "password1") and not store.authenticate("alice", "password2")
    assert "mapping" not in store.get("alice", "1")[0]
    assert store.get("bob", "1")[1] is None
    side = {f: False for f in FIELDS} | {
        "evidence": "history has no reference to progress",
        "explanation": "missed prior progress",
    }
    review = {"a": side, "b": side, "preference": "tie"}
    store.save("alice", "1", review, True)
    assert store.get("bob", "1")[1] is None
    with pytest.raises(PermissionError):
        store.mappings()
    with pytest.raises(ValueError):
        store.save("alice", "1", review)
    store.save("bob", "1", review, True)
    assert store.mappings()["1"]["a"] == "evolution"


def test_incomplete_cannot_lock(tmp_path):
    store = ReviewStore(tmp_path / "r.db")
    packet = {
        "id": "1",
        "case_id": "c",
        "checkpoint_id": "t",
        "public_history": "h",
        "response_a": "a",
        "response_b": "b",
        "mapping": {},
    }
    store.initialize({"a": "x", "b": "y"}, [packet])
    with pytest.raises(ValueError):
        store.save("a", "1", {"a": {}, "b": {}}, True)


def test_authenticated_pages_render_without_mapping(tmp_path):
    from fastapi.testclient import TestClient

    from tracemind.web.app import create_app

    store = ReviewStore(tmp_path / "render.db")
    packet = {
        "id": "1",
        "case_id": "c",
        "checkpoint_id": "t",
        "public_history": "history",
        "response_a": "first reply",
        "response_b": "second reply",
        "mapping": {"a": "evolution", "b": "summary"},
    }
    store.initialize({"alice": "one-password", "bob": "other-password"}, [packet])
    with TestClient(create_app(store, "x" * 40)) as client:
        assert client.get("/").status_code == 401
        assert (
            client.post(
                "/login", data={"identity": "alice", "password": "one-password"}
            ).status_code
            == 200
        )
        response = client.get("/packet/1")
        assert response.status_code == 200
        assert "first reply" in response.text and "evolution" not in response.text
        assert client.get("/mapping").status_code == 403
