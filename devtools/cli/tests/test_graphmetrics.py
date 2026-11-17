"""Per-function numbers for the readable Graph tab: references, dependents, reach, tests, calls, git changes, pace."""
import json, os

from conftest import git, repomap
from repomap import graph, graphmetrics


def numbers(repo, **kwargs):
    previous_dir = os.getcwd()
    os.chdir(repo)
    try:
        res = graphmetrics.metrics(graph.connect(), graphmetrics.load_memory(), **kwargs)
    finally:
        os.chdir(previous_dir)
    return res, {row["key"]: row for row in res["functions"]}


def edit_round_cents(repo, note):
    path = repo / "src/shopkit/money.py"
    path.write_text(path.read_text().replace('    return float(Decimal', f'    # {note}\n    return float(Decimal'))
    git(repo, "commit", "-qam", note)


def test_pipelines_reach_further_than_helpers(shop):
    """receipt runs total, which runs the cart, discounts and tax: 9 functions run underneath it. round_cents is
    called from 4 places but runs nothing underneath, so it has more references and less reach."""
    repomap(shop, "graph", "update")
    _, rows = numbers(shop)
    receipt, rounding = rows["shopkit/checkout.py:receipt"], rows["shopkit/money.py:round_cents"]
    assert (receipt["reach"], receipt["reach_from"]) == (9, "tests")
    assert rounding["reach"] == 0
    assert rounding["references"] > receipt["references"]


def test_hand_checked_values(shop):
    """Catalog.price_of: called by Cart.subtotal (from inside a genexpr, folded into subtotal) in tests and by
    to_csv in source only, so 2 functions plus the 2 tests that call it directly refer to it. Through total and
    receipt above subtotal, 4 functions depend on it."""
    repomap(shop, "graph", "update")
    _, rows = numbers(shop)
    price_of = rows["shopkit/catalog.py:Catalog.price_of"]
    assert price_of["dependents"] == 4
    assert price_of["references"] == 4
    assert price_of["tests"] == 7 and price_of["calls"] == 8
    assert not any("<locals>" in key for key in rows)  # nested code folds into its function


def test_untested_code_reaches_through_source_arrows(shop):
    """No test runs to_csv: it is marked untested, and its reach comes from the source arrow to price_of."""
    repomap(shop, "graph", "update")
    _, rows = numbers(shop)
    to_csv = rows["shopkit/export.py:to_csv"]
    assert (to_csv["tested"], to_csv["reach"], to_csv["reach_from"], to_csv["tests"]) == (False, 1, "source", 0)
    assert rows["shopkit/cart.py:<module>"]["tested"]  # module code ran at import


def test_git_window_counts_commits_that_edit_a_function(shop):
    """Two edits to round_cents make it changed in 3 commits (with the first one) and fast; format_price, in the
    same file but untouched since, stays at 1. A window that starts tomorrow sees nothing, and all history
    sees every commit."""
    edit_round_cents(shop, "First")
    edit_round_cents(shop, "Second")
    repomap(shop, "graph", "update")
    res, rows = numbers(shop)
    assert res["commits"] == 3
    assert (rows["shopkit/money.py:round_cents"]["changes"], rows["shopkit/money.py:round_cents"]["pace"]) == (3, "fast")
    assert rows["shopkit/money.py:format_price"]["changes"] == 1
    future, future_rows = numbers(shop, since="tomorrow")
    assert future["commits"] == 0 and not any(row["changes"] for row in future_rows.values())
    everything, _ = numbers(shop, since=None)
    assert (everything["commits"], everything["since"]) == (3, "all history")


def test_cli_json_and_filter(shop):
    """'repomap graph metrics KEY --json' returns just the matching functions, sorted by the chosen number."""
    repomap(shop, "graph", "update")
    res = json.loads(repomap(shop, "graph", "metrics", "checkout", "--sort", "references", "--json"))
    keys = [row["key"] for row in res["functions"]]
    assert set(keys) == {"shopkit/checkout.py:total", "shopkit/checkout.py:receipt", "shopkit/checkout.py:<module>"}
    assert keys[0] == "shopkit/checkout.py:total"
    assert "reach" in repomap(shop, "graph", "metrics", "--all")
