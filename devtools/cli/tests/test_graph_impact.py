"""Impact computed on the code graph: the same tests as the trace, plus downstream code, public API and gaps."""
import json

import pytest

from conftest import repomap
from repomap import graph


@pytest.fixture
def g(shop):
    repomap(shop, "graph", "update")
    return shop


def imp(shop, *a):
    return json.loads(repomap(shop, "impact", "--graph", "--json", *a))


def edit_two(shop):
    p = shop / "src/shopkit/money.py"
    p.write_text(p.read_text().replace("rounding=ROUND_HALF_UP)", "rounding=ROUND_HALF_UP) + 0"))
    p = shop / "src/shopkit/catalog.py"
    p.write_text(p.read_text().replace("return self._prices[sku]", "return self._prices[sku] * 1"))


def test_same_tests_as_the_trace_based_impact(g):
    edit_two(g)
    new = imp(g)
    old = json.loads(repomap(g, "impact", "--json"))
    sure = [t["test"] for t in new["tests"] if t["reason"] == "runs changed code"]
    assert sorted(sure) == old["tests"] and len(sure) == 9


def test_tests_are_ranked_closest_first(g):
    edit_two(g)
    tests = imp(g)["tests"]
    depths = [t["depth"] for t in tests]
    assert depths == sorted(depths) and depths[0] == 1
    by = {t["test"]: t["depth"] for t in tests}
    assert by["tests/test_money.py::test_round_half_up"] == 1
    assert by["tests/test_checkout.py::test_total_with_tax"] == 2  # total -> round_cents
    assert by["tests/test_checkout.py::test_receipt"] == 3  # receipt -> total -> round_cents


def test_downstream_with_confidence_and_source(g):
    edit_two(g)
    down = {d["key"]: d for d in imp(g)["downstream"]}
    assert down["shopkit/checkout.py:total"]["confidence"] == 0.9
    assert down["shopkit/checkout.py:receipt"]["confidence"] == 0.81
    assert down["shopkit/checkout.py:receipt"]["via"] == "shopkit/checkout.py:total"
    # untested to_csv is reached only through a name-guessed source arrow: 0.9 * 0.5 * 0.8
    assert down["shopkit/export.py:to_csv"] == {"key": "shopkit/export.py:to_csv", "confidence": 0.36,
                                                "via": "shopkit/catalog.py:Catalog.price_of", "source": "static"}
    # genexprs and other nested code roll into the function that holds them
    assert not any("<locals>" in k for k in down)


def test_public_and_untested(g):
    edit_two(g)
    r = imp(g)
    assert "shopkit/checkout.py:receipt" in r["public"] and "shopkit/export.py:to_csv" in r["public"]
    assert r["untested"] == ["shopkit/export.py:to_csv"]


def test_naming_an_untested_function(g):
    """The trace-only impact can't see to_csv at all; the graph finds what it calls through, and that no test runs it."""
    r = imp(g, "to_csv")
    assert r["changed"] == ["shopkit/export.py:to_csv"] and r["unmatched"] == []
    assert r["tests"] == [] and r["untested"] == ["shopkit/export.py:to_csv"]


def test_cutoff_and_levels(g):
    edit_two(g)
    assert "shopkit/export.py:to_csv" not in {d["key"] for d in imp(g, "--cutoff", "0.5")["downstream"]}
    mods = imp(g, "--level", "module")
    assert mods["changed"] == ["shopkit/catalog.py", "shopkit/money.py"]
    assert {d["key"] for d in mods["downstream"]} == {"shopkit/cart.py", "shopkit/checkout.py", "shopkit/tax.py",
                                                      "shopkit/export.py"}
    classes = imp(g, "--level", "class")
    assert "shopkit/cart.py:Cart" in {d["key"] for d in classes["downstream"]}


def test_module_level_change_reaches_the_file(g):
    p = g / "src/shopkit/tax.py"
    p.write_text(p.read_text().replace('"CA": 0.0725', '"CA": 0.08'))
    r = imp(g)
    assert r["changed"] == ["shopkit/tax.py:<module>"]
    assert {t["test"] for t in r["tests"]} >= {"tests/test_checkout.py::test_total_with_tax"}
    assert "shopkit/checkout.py:total" in {d["key"] for d in r["downstream"]}


def test_text_output(g):
    edit_two(g)
    out = repomap(g, "impact", "--graph")
    assert "Tests to run  (9 of 18 run changed code; closest first)" in out
    assert "0.36  shopkit/export.py:to_csv   via shopkit/catalog.py:Catalog.price_of  seen only in source" in out
    assert "No test covers\n  shopkit/export.py:to_csv" in out


def test_is_public():
    assert graph.is_public("pkg/mod.py:run") and graph.is_public("pkg/mod.py:Cart.__init__")
    assert not graph.is_public("pkg/_impl.py:run") and not graph.is_public("pkg/mod.py:_helper")
    assert not graph.is_public("pkg/mod.py:f.<locals>.g") and not graph.is_public("pkg/mod.py:<module>")
    assert graph.is_public("pkg/__init__.py:run")
