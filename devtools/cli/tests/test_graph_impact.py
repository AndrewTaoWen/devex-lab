"""Impact computed on the code graph: the same tests as the trace, plus downstream code, public API and gaps."""
import json

import pytest

from conftest import repomap
from repomap import graph


@pytest.fixture
def shop_graph(shop):
    repomap(shop, "graph", "update")
    return shop


def graph_impact(shop, *extra_args):
    return json.loads(repomap(shop, "impact", "--graph", "--json", *extra_args))


def edit_two(shop):
    path = shop / "src/shopkit/money.py"
    path.write_text(path.read_text().replace("rounding=ROUND_HALF_UP)", "rounding=ROUND_HALF_UP) + 0"))
    path = shop / "src/shopkit/catalog.py"
    path.write_text(path.read_text().replace("return self._prices[sku]", "return self._prices[sku] * 1"))


def test_same_tests_as_the_trace_based_impact(shop_graph):
    """For the same edit, the graph picks exactly the same 9 tests as the older trace-based impact, so switching
    to the graph loses nothing."""
    edit_two(shop_graph)
    new = graph_impact(shop_graph)
    old = json.loads(repomap(shop_graph, "impact", "--json"))
    sure = [test["test"] for test in new["tests"] if test["reason"] == "runs changed code"]
    assert sorted(sure) == old["tests"] and len(sure) == 9


def test_tests_are_ranked_closest_first(shop_graph):
    """Tests are ordered by how close they call the changed code: a test that calls it directly comes before one
    that reaches it through one or two other functions."""
    edit_two(shop_graph)
    tests = graph_impact(shop_graph)["tests"]
    depths = [test["depth"] for test in tests]
    assert depths == sorted(depths) and depths[0] == 1
    depth_of = {test["test"]: test["depth"] for test in tests}
    assert depth_of["tests/test_money.py::test_round_half_up"] == 1
    assert depth_of["tests/test_checkout.py::test_total_with_tax"] == 2  # total -> round_cents
    assert depth_of["tests/test_checkout.py::test_receipt"] == 3  # receipt -> total -> round_cents


def test_downstream_with_confidence_and_source(shop_graph):
    """Code that calls the changed code gets a confidence that drops with each step away (0.9, then 0.81), and
    says which function it goes through. A link only read from the source counts for less (0.36) and is marked
    as such. Code nested inside a function is folded into that function."""
    edit_two(shop_graph)
    down = {dot["key"]: dot for dot in graph_impact(shop_graph)["downstream"]}
    assert down["shopkit/checkout.py:total"]["confidence"] == 0.9
    assert down["shopkit/checkout.py:receipt"]["confidence"] == 0.81
    assert down["shopkit/checkout.py:receipt"]["via"] == "shopkit/checkout.py:total"
    # untested to_csv is reached only through a name-guessed source arrow: 0.9 * 0.5 * 0.8
    assert down["shopkit/export.py:to_csv"] == {"key": "shopkit/export.py:to_csv", "confidence": 0.36,
                                                "via": "shopkit/catalog.py:Catalog.price_of", "source": "static"}
    # genexprs and other nested code roll into the function that holds them
    assert not any("<locals>" in key for key in down)


def test_public_and_untested(shop_graph):
    """The result lists the public functions a package user could notice the change through, and the affected
    code that no test runs."""
    edit_two(shop_graph)
    result = graph_impact(shop_graph)
    assert "shopkit/checkout.py:receipt" in result["public"] and "shopkit/export.py:to_csv" in result["public"]
    assert result["untested"] == ["shopkit/export.py:to_csv"]


def test_naming_an_untested_function(shop_graph):
    """The older impact can't see to_csv at all, because no test runs it. The graph still finds it, and reports
    that no test covers it."""
    result = graph_impact(shop_graph, "to_csv")
    assert result["changed"] == ["shopkit/export.py:to_csv"] and result["unmatched"] == []
    assert result["tests"] == [] and result["untested"] == ["shopkit/export.py:to_csv"]


def test_cutoff_and_levels(shop_graph):
    """Raising the cutoff drops weak links like to_csv. The same result can be zoomed out to modules or classes."""
    edit_two(shop_graph)
    strict = graph_impact(shop_graph, "--cutoff", "0.5")
    assert "shopkit/export.py:to_csv" not in {dot["key"] for dot in strict["downstream"]}
    modules = graph_impact(shop_graph, "--level", "module")
    assert modules["changed"] == ["shopkit/catalog.py", "shopkit/money.py"]
    assert {dot["key"] for dot in modules["downstream"]} == {"shopkit/cart.py", "shopkit/checkout.py", "shopkit/tax.py",
                                                      "shopkit/export.py"}
    classes = graph_impact(shop_graph, "--level", "class")
    assert "shopkit/cart.py:Cart" in {dot["key"] for dot in classes["downstream"]}


def test_module_level_change_reaches_the_file(shop_graph):
    """Changing a constant at the top of a file reaches the functions in that file and the tests that use them,
    even with no call arrow involved."""
    path = shop_graph / "src/shopkit/tax.py"
    path.write_text(path.read_text().replace('"CA": 0.0725', '"CA": 0.08'))
    result = graph_impact(shop_graph)
    assert result["changed"] == ["shopkit/tax.py:<module>"]
    assert {test["test"] for test in result["tests"]} >= {"tests/test_checkout.py::test_total_with_tax"}
    assert "shopkit/checkout.py:total" in {dot["key"] for dot in result["downstream"]}


def test_text_output(shop_graph):
    """The plain-text output shows how many tests to run, the code that may behave differently with its
    confidence, and the code no test covers."""
    edit_two(shop_graph)
    out = repomap(shop_graph, "impact", "--graph")
    assert "Tests to run  (9 of 18 run changed code; closest first)" in out
    assert "0.36  shopkit/export.py:to_csv   via shopkit/catalog.py:Catalog.price_of  seen only in source" in out
    assert "No test covers\n  shopkit/export.py:to_csv" in out


def test_is_public():
    """A function counts as public unless something in its path or name starts with an underscore, it is nested
    inside another function, or it is module-level code. Dunder methods like __init__ are public."""
    assert graph.is_public("pkg/mod.py:run") and graph.is_public("pkg/mod.py:Cart.__init__")
    assert not graph.is_public("pkg/_impl.py:run") and not graph.is_public("pkg/mod.py:_helper")
    assert not graph.is_public("pkg/mod.py:f.<locals>.shop_graph") and not graph.is_public("pkg/mod.py:<module>")
    assert graph.is_public("pkg/__init__.py:run")
