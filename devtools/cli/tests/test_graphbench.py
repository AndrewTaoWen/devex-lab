"""The graph scorecard: what counts as behaving differently, the ranking metric, and an end-to-end run."""
import json, os, sys

from conftest import CLI, repomap

sys.path.insert(0, str(CLI))
import graphbench  # noqa: E402


def calls(*rows):
    return {"t::a": {"steps": len(rows), "truncated": False, "rows": [[i, 1, None, *r] for i, r in enumerate(rows)]}}


def test_behaviour_ignores_memory_addresses():
    a = calls(("m.py:f", {"x": {"type": "C", "repr": "<C at 0x7f01>"}}, {"type": "int", "repr": "1"}, None))
    b = calls(("m.py:f", {"x": {"type": "C", "repr": "<C at 0x9abc>"}}, {"type": "int", "repr": "1"}, None))
    assert graphbench.differs(graphbench.behaviour(a), graphbench.behaviour(b)) == []


def test_differs_sees_new_values_exceptions_and_missing_calls():
    base = graphbench.behaviour(calls(("m.py:f", {}, {"type": "int", "repr": "1"}, None),
                                      ("m.py:g", {}, {"type": "int", "repr": "2"}, None),
                                      ("m.py:h", {}, None, None)))
    now = graphbench.behaviour(calls(("m.py:f", {}, {"type": "int", "repr": "9"}, None),
                                     ("m.py:g", {}, None, "KeyError")))
    assert graphbench.differs(base, now) == ["m.py:f", "m.py:g", "m.py:h"]


def test_noise_between_clean_runs_is_ignored():
    a = graphbench.behaviour(calls(("m.py:now", {}, {"type": "float", "repr": "1.0"}, None),
                                   ("m.py:f", {}, {"type": "int", "repr": "1"}, None)))
    b = graphbench.behaviour(calls(("m.py:now", {}, {"type": "float", "repr": "2.0"}, None),
                                   ("m.py:f", {}, {"type": "int", "repr": "1"}, None)))
    noise = graphbench.noisy(a, b)
    c = graphbench.behaviour(calls(("m.py:now", {}, {"type": "float", "repr": "3.0"}, None),
                                   ("m.py:f", {}, {"type": "int", "repr": "5"}, None)))
    assert graphbench.differs(a, c, noise) == ["m.py:f"]


def test_only_package_functions_count(shop):
    from repomap import graph
    repomap(shop, "graph", "update")
    db = graph.connect(shop / ".repomap/graph.db")
    r = dict(function="shopkit/money.py:round_cents", kind="return None", broken_tests=[],
             behaved_differently=["shopkit/tax.py:tax_for", "../../tmp/pytest-1/x/a.py:<module>"])
    assert graphbench.score_one(db, r, 18)["truth"] == 1


def test_first_fail_and_rollup():
    assert graphbench.first_fail(["a", "b", "c"], {"c", "b"}) == 2
    assert graphbench.first_fail(["a"], {"z"}) is None
    assert graphbench.rollup_key("m.py:C.f.<locals>.<genexpr>") == "m.py:C.f"


def test_truth_and_score_on_shopkit(shop):
    repomap(shop, "graph", "update")
    old = os.getcwd()
    os.chdir(shop)
    try:
        graphbench.main(["truth", "--n", "4", "--seed", "3", "--out", "gb.json"])
        res = json.loads((shop / "gb.json").read_text())
        out = graphbench.score(type("A", (), {"results": "gb.json", "mutbench": None, "json": False})())
    finally:
        os.chdir(old)
    assert len(res) == 4 and any(r["broken_tests"] for r in res)
    s = out["summary"]
    assert s["broken_selected"] == s["broken_tests"]  # the graph never drops a test that ran the change
    # every truly affected caller is listed: the graph walks exactly that direction
    assert sum(x["hit"] for x in out["faults"]) >= s["truth_in_callers"]
    # the source files are back to normal afterwards: a clean run passes
    assert "18 passed" in __import__("subprocess").run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=shop, capture_output=True,
        text=True, env={**os.environ, "PYTHONPATH": str(shop / "src")}).stdout
