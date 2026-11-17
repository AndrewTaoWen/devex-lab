"""Test replay: the recorded call order for a test, with the values passed and returned, served to the page."""
import json, os, sys

from conftest import repomap
from test_serve_graph import get, page_check, server  # noqa: F401  (server is a fixture)
from repomap import cli

RECEIPT = "tests/test_checkout.py::test_receipt"


def in_repo(repo, fn):
    previous_dir = os.getcwd()
    os.chdir(repo)
    try:
        return fn()
    finally:
        os.chdir(previous_dir)


def test_replay_lists_tests_and_returns_calls_in_order(server):
    """/api/replay lists every test with a recorded call order. For test_receipt it returns the calls in order:
    the test calls receipt, receipt calls total, and each call carries its arguments and return value."""
    tests = json.loads(get(server + "/api/replay"))
    assert [RECEIPT, 18, False] in tests
    run = json.loads(get(server + "/api/replay?test=" + RECEIPT))
    assert (run["steps"], run["truncated"]) == (18, False)
    rows = run["rows"]
    assert [row["seq"] for row in rows] == list(range(18))
    receipt = next(row for row in rows if row["function"] == "shopkit/checkout.py:receipt")
    total = next(row for row in rows if row["function"] == "shopkit/checkout.py:total")
    assert receipt["parent_seq"] is None and total["parent_seq"] == receipt["seq"] and total["depth"] == 2
    assert total["args"]["region"]["repr"] and total["ret"]["type"] == "float"


def test_a_capped_recording_says_it_was_cut(shop):
    """Recording with --steps 3 keeps the first 3 calls of each test and marks longer runs as cut short."""
    repomap(shop, "record", "--package", "src/shopkit", "--python", sys.executable, "--steps", "3")
    repomap(shop, "graph", "update")
    run = in_repo(shop, lambda: cli.ui_replay(RECEIPT))
    assert (len(run["rows"]), run["truncated"]) == (3, True)


def test_unknown_test_is_an_empty_run(shop):
    repomap(shop, "graph", "update")
    assert in_repo(shop, lambda: cli.ui_replay("tests/nope.py::test_nothing")) == dict(
        test="tests/nope.py::test_nothing", steps=0, truncated=False, rows=[])


def test_export_carries_replays_unless_too_big(shop, monkeypatch):
    """An exported page carries every test's replay, but not when the total would make the page too big."""
    repomap(shop, "graph", "update")
    everything = in_repo(shop, cli.ui_replay_all)
    assert RECEIPT in everything and everything[RECEIPT]["steps"] == 18
    monkeypatch.setattr(cli, "REPLAY_EMBED_ROWS", 10)
    assert in_repo(shop, cli.ui_replay_all) is None


def test_page_steps_through_a_test(server):
    """In the browser, the replay starts at the first call; after 5 steps of test_receipt, the counter, the lit
    cell and the call text all point at the same call, and the tree has one bar per call."""
    report = page_check(server, "replay", RECEIPT, "5")
    assert report["bars"] == 18 and report["counter"].startswith("call 6 of 18")
    assert len(report["current"]) == 1
    assert "returned" in report["step"] or "raised" in report["step"]
    assert report["current"][0].split(":")[1].split(".")[-1] in report["step"]
