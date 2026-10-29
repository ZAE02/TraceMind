from tracemind.evaluation import (
    agreement,
    cluster_bootstrap,
    make_packets,
    public_packet,
    summarize,
)


def test_packet_plan_and_blinding():
    candidates = {
        k: k + " answer" for k in ("evolution", "temporalrag", "on_demand", "summary", "current")
    }

    def item(i):
        return {
            "group_id": str(i // 12),
            "case_id": str(i // 3),
            "checkpoint_id": str(i),
            "public_history": "history",
            "candidates": candidates,
            "visible_input": "secret",
        }

    current = [item(g * 12 + j) for g in range(5) for j in range(4)]
    packets = make_packets([item(i) for i in range(60)], current, 42)
    assert len(packets) == 200
    assert sum("current" in p["mapping"].values() for p in packets) == 20
    assert all(
        "mapping" not in public_packet(p) and "visible_input" not in public_packet(p)
        for p in packets
    )
    assert packets == make_packets([item(i) for i in range(60)], current, 42)


def test_metrics_exclude_access_limited_and_cluster():
    rows = [
        {"group_id": "a", "unsupported_assertion": True},
        {"group_id": "b", "unsupported_assertion": False},
        {"group_id": "a", "unsupported_assertion": True, "access_limited": True},
    ]
    assert summarize(rows)["denominator"] == 2
    assert summarize(rows)["counts"]["unsupported_assertion"] == 1
    assert cluster_bootstrap(rows, "unsupported_assertion", 100, 2) == (0.0, 1.0)
    assert (
        agreement([(rows[0], rows[0]), (rows[0], rows[2])], "unsupported_assertion")["denominator"]
        == 1
    )


def test_tentative_exploration_is_not_an_error():
    result = summarize([{"group_id": "a", "tentative_exploration": True}])
    assert "tentative_exploration" not in result["counts"]
    assert result["tentative_exploration_count"] == 1


def test_access_is_attributed_per_error_type():
    row = {
        "group_id": "g",
        "unsupported_assertion": True,
        "missed_progress": True,
        "access_limited": {"missed_progress": True},
    }
    result = summarize([row])
    assert result["counts"]["unsupported_assertion"] == 1
    assert result["denominators"]["missed_progress"] == 0
    assert result["rates"]["missed_progress"] is None


def test_current_packet_cannot_swap_frozen_history():
    import copy

    import pytest

    def item(i):
        return {
            "group_id": str(i // 12),
            "case_id": str(i // 3),
            "checkpoint_id": str(i),
            "public_history": "same history",
            "candidates": {
                m: "reply " + m
                for m in ("current", "evolution", "temporalrag", "summary", "on_demand")
            },
        }

    items = [item(i) for i in range(60)]
    selected = [copy.deepcopy(items[g * 12 + j]) for g in range(5) for j in range(4)]
    selected[0]["public_history"] = "swapped history"
    with pytest.raises(ValueError, match="frozen checkpoint"):
        make_packets(items, selected)
