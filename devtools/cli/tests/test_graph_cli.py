"""`repomap graph update|show` and the recompute rules on the shopkit example."""
import json, sqlite3

from conftest import git, repomap


def update(shop, *a):
    return json.loads(repomap(shop, "graph", "update", "--json", *a))


def test_first_update_builds_the_graph(shop):
    r = update(shop)
    assert r["source"] == "built from scratch" and r["retrace"] == "nothing to re-trace"
    assert r["graph"]["tests"] == 18 and r["graph"]["call_rows"] > 0
    assert (shop / ".repomap/graph.db").exists()


def test_edit_retraces_only_its_tests(shop):
    update(shop)
    p = shop / "src/shopkit/money.py"
    p.write_text(p.read_text().replace('symbol = {"USD": "$"', 'symbol = {"USD": "US$"'))
    r = update(shop)
    assert r["retrace"].startswith("re-traced ") and "touching 1 changed files" in r["retrace"]
    assert int(r["retrace"].split()[1]) < 18
    assert r["source"] == "re-read 1 changed files"
    db = sqlite3.connect(shop / ".repomap/graph.db")
    ret = db.execute("SELECT ret FROM test_calls WHERE test = 'tests/test_money.py::test_format_usd'").fetchone()[0]
    assert "US$" in ret


def test_conftest_change_runs_a_full_retrace(shop):
    update(shop)
    (shop / "tests/conftest.py").write_text("# shared fixtures\n")
    r = update(shop)
    assert r["retrace"].startswith("full re-trace (test setup changed: tests/conftest.py")
    # after the full trace the rule counts from the new snapshot, so it doesn't fire again
    assert update(shop)["retrace"] == "nothing to re-trace"


def test_no_full_only_reports(shop):
    update(shop)
    p = shop / "pyproject.toml"
    p.write_text("[project]\nname = 'shopkit'\n")
    r = update(shop, "--no-full")
    assert r["full_due"] and r["reasons"] == ["dependencies changed: pyproject.toml"]
    assert "Full re-trace due: dependencies changed" in repomap(shop, "status")


def test_commits_since_full(shop):
    update(shop)
    for i in range(3):
        (shop / f"notes{i}.txt").write_text("x")
        git(shop, "add", "-A")
        git(shop, "commit", "-qm", f"c{i}")
    show = json.loads(repomap(shop, "graph", "show", "--json", "--max-commits", "2"))
    assert show["full_due"] and "3 commits since the last full trace" in show["reasons"][0]


def test_show_summary_and_a_dot(shop):
    update(shop)
    s = json.loads(repomap(shop, "graph", "show", "--json"))
    assert "shopkit/export.py:to_csv" in s["source_only"] and s["changed_since_built"] == []
    out = repomap(shop, "graph", "show", "price_of")
    assert "used by  shopkit/export.py:to_csv  (calls_by_name, static)" in out
    assert "used by  shopkit/cart.py:Cart.subtotal  (calls, runtime)" in out  # genexpr rolled into subtotal
    mods = json.loads(repomap(shop, "graph", "show", "shopkit/tax.py", "--level", "module", "--json"))
    assert ["shopkit/money.py", "calls", "runtime"] in mods["shopkit/tax.py"]["callees"]
