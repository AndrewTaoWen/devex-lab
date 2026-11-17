"""The graph scorecard: what counts as behaving differently, the ranking metric, and an end-to-end run."""
import json, os, sys

from conftest import CLI, repomap

sys.path.insert(0, str(CLI))
import graphbench  # noqa: E402


def calls(*rows):
    rows = [[seq, 1, None, *row] for seq, row in enumerate(rows)]
    return {"t::a": {"steps": len(rows), "truncated": False, "rows": rows}}


def test_behaviour_ignores_memory_addresses():
    """Two runs that differ only in a memory address (0x7f01 vs 0x9abc) count as the same behaviour."""
    first = calls(("m.py:f", {"x": {"type": "C", "repr": "<C at 0x7f01>"}}, {"type": "int", "repr": "1"}, None))
    second = calls(("m.py:f", {"x": {"type": "C", "repr": "<C at 0x9abc>"}}, {"type": "int", "repr": "1"}, None))
    assert graphbench.differs(graphbench.behaviour(first), graphbench.behaviour(second)) == []


def test_differs_sees_new_values_exceptions_and_missing_calls():
    """A function counts as behaving differently if it returns a new value, raises where it used to return, or
    stops being called at all."""
    base = graphbench.behaviour(calls(("m.py:f", {}, {"type": "int", "repr": "1"}, None),
                                      ("m.py:g", {}, {"type": "int", "repr": "2"}, None),
                                      ("m.py:h", {}, None, None)))
    now = graphbench.behaviour(calls(("m.py:f", {}, {"type": "int", "repr": "9"}, None),
                                     ("m.py:g", {}, None, "KeyError")))
    assert graphbench.differs(base, now) == ["m.py:f", "m.py:g", "m.py:h"]


def test_noise_between_clean_runs_is_ignored():
    """A function that differs between two clean runs (one that reads the clock, say) is ignored, so only real
    changes from the fault are counted."""
    first_clean = graphbench.behaviour(calls(("m.py:now", {}, {"type": "float", "repr": "1.0"}, None),
                                   ("m.py:f", {}, {"type": "int", "repr": "1"}, None)))
    second_clean = graphbench.behaviour(calls(("m.py:now", {}, {"type": "float", "repr": "2.0"}, None),
                                   ("m.py:f", {}, {"type": "int", "repr": "1"}, None)))
    noise = graphbench.noisy(first_clean, second_clean)
    mutant = graphbench.behaviour(calls(("m.py:now", {}, {"type": "float", "repr": "3.0"}, None),
                                   ("m.py:f", {}, {"type": "int", "repr": "5"}, None)))
    assert graphbench.differs(first_clean, mutant, noise) == ["m.py:f"]


def test_only_package_functions_count(shop):
    """Only the package's own functions count as affected. A module a test wrote to a temp folder is left out."""
    from repomap import graph
    repomap(shop, "graph", "update")
    db = graph.connect(shop / ".repomap/graph.db")
    fault = dict(function="shopkit/money.py:round_cents", kind="return None", broken_tests=[],
             behaved_differently=["shopkit/tax.py:tax_for", "../../tmp/pytest-1/x/a.py:<module>"])
    assert graphbench.score_one(db, fault, 18)["truth"] == 1


def test_first_fail_and_rollup():
    """first_fail gives the position of the first broken test in a list, or None if there is none. Code nested
    inside a function counts as that function."""
    assert graphbench.first_fail(["a", "b", "c"], {"c", "b"}) == 2
    assert graphbench.first_fail(["a"], {"z"}) is None
    assert graphbench.rollup_key("m.py:C.f.<locals>.<genexpr>") == "m.py:C.f"


def test_truth_and_score_on_shopkit(shop):
    """A full run on shopkit: inject 4 faults, record what changed, and score the graph. The graph must pick
    every broken test and list every affected caller, and the source files must be restored afterwards."""
    repomap(shop, "graph", "update")
    previous_dir = os.getcwd()
    os.chdir(shop)
    try:
        graphbench.main(["truth", "--n", "4", "--seed", "3", "--out", "gb.json"])
        faults = json.loads((shop / "gb.json").read_text())
        scored = graphbench.score(type("A", (), {"results": "gb.json", "mutbench": None, "json": False})())
    finally:
        os.chdir(previous_dir)
    assert len(faults) == 4 and any(fault["broken_tests"] for fault in faults)
    stats = scored["summary"]
    assert stats["broken_selected"] == stats["broken_tests"]  # the graph never drops a test that ran the change
    # every truly affected caller is listed: the graph walks exactly that direction
    assert sum(row["hit"] for row in scored["faults"]) >= stats["truth_in_callers"]
    # the source files are back to normal afterwards: a clean run passes
    assert "18 passed" in __import__("subprocess").run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=shop, capture_output=True,
        text=True, env={**os.environ, "PYTHONPATH": str(shop / "src")}).stdout
