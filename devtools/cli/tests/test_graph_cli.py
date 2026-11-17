"""'repomap graph update|show' and the recompute rules on the shopkit example."""
import json, sqlite3

from conftest import git, repomap


def update(shop, *extra_args):
    return json.loads(repomap(shop, "graph", "update", "--json", *extra_args))


def test_first_update_builds_the_graph(shop):
    """The first 'repomap graph update' builds the graph file from scratch, with all 18 tests and their call
    order, and doesn't need to re-run any tests."""
    result = update(shop)
    assert result["source"] == "built from scratch" and result["retrace"] == "nothing to re-trace"
    assert result["graph"]["tests"] == 18 and result["graph"]["call_rows"] > 0
    assert (shop / ".repomap/graph.db").exists()


def test_edit_retraces_only_its_tests(shop):
    """After one file is edited, update re-runs only the tests that touch that file, re-reads only that file's
    source, and the stored values show the new behaviour."""
    update(shop)
    path = shop / "src/shopkit/money.py"
    path.write_text(path.read_text().replace('symbol = {"USD": "$"', 'symbol = {"USD": "US$"'))
    result = update(shop)
    assert result["retrace"].startswith("re-traced ") and "touching 1 changed files" in result["retrace"]
    assert int(result["retrace"].split()[1]) < 18
    assert result["source"] == "re-read 1 changed files"
    db = sqlite3.connect(shop / ".repomap/graph.db")
    ret = db.execute("SELECT ret FROM test_calls WHERE test = 'tests/test_money.py::test_format_usd'").fetchone()[0]
    assert "US$" in ret


def test_conftest_change_runs_a_full_retrace(shop):
    """Changing the shared test setup (conftest.py) can change what every test runs, so update re-runs the whole
    suite. The next update then has nothing left to do."""
    update(shop)
    (shop / "tests/conftest.py").write_text("# shared fixtures\n")
    result = update(shop)
    assert result["retrace"].startswith("full re-trace (test setup changed: tests/conftest.py")
    # after the full trace the rule counts from the new snapshot, so it doesn't fire again
    assert update(shop)["retrace"] == "nothing to re-trace"


def test_no_full_only_reports(shop):
    """With --no-full, a dependency change doesn't trigger a full re-run. It is only reported, both by update
    and by 'repomap status'."""
    update(shop)
    path = shop / "pyproject.toml"
    path.write_text("[project]\nname = 'shopkit'\n")
    result = update(shop, "--no-full")
    assert result["full_due"] and result["reasons"] == ["dependencies changed: pyproject.toml"]
    assert "Full re-trace due: dependencies changed" in repomap(shop, "status")


def test_commits_since_full(shop):
    """When more commits than the limit have passed since the last full run, 'graph show' says a full re-run is
    due and why."""
    update(shop)
    for i in range(3):
        (shop / f"notes{i}.txt").write_text("x")
        git(shop, "add", "-A")
        git(shop, "commit", "-qm", f"c{i}")
    show = json.loads(repomap(shop, "graph", "show", "--json", "--max-commits", "2"))
    assert show["full_due"] and "3 commits since the last full trace" in show["reasons"][0]


def test_show_summary_and_a_dot(shop):
    """'graph show' lists what the graph holds, including functions no test runs. Given a name, it shows who
    uses that function and what it uses, at any zoom level."""
    update(shop)
    summary = json.loads(repomap(shop, "graph", "show", "--json"))
    assert "shopkit/export.py:to_csv" in summary["source_only"] and summary["changed_since_built"] == []
    out = repomap(shop, "graph", "show", "price_of")
    assert "used by  shopkit/export.py:to_csv  (calls_by_name, static)" in out
    assert "used by  shopkit/cart.py:Cart.subtotal  (calls, runtime)" in out  # genexpr rolled into subtotal
    modules = json.loads(repomap(shop, "graph", "show", "shopkit/tax.py", "--level", "module", "--json"))
    assert ["shopkit/money.py", "calls", "runtime"] in modules["shopkit/tax.py"]["callees"]
