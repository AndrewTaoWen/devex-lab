"""The graph store: runtime arrows win, static arrows fill untested code, roll-ups, and per-file updates."""
import json, os, sqlite3

import pytest

from repomap import graph


@pytest.fixture
def built(shop, tmp_path):
    m = json.loads((shop / ".repomap/memory.json").read_text())
    calls = json.loads((shop / ".repomap/calls.json").read_text())
    db = graph.build(shop / "src/shopkit", m, calls, path=tmp_path / "graph.db")
    return shop, db, m, calls


def rows(db, sql, *args):
    return db.execute(sql, args).fetchall()


def test_both_layers_are_stored(built):
    _, db, m, _ = built
    s = graph.summary(db)
    assert s["traced"] == len(m["functions"])
    assert s["runtime_arrows"] > 0 and s["static_arrows"] > 0
    assert s["tests"] == 18


def test_runtime_is_truth_static_is_fallback(built):
    _, db, _, _ = built
    eff = {(a, b, src) for a, b, _, src in graph.arrows(db, level="line")}
    # total ran in tests: its calls come from the runtime layer only
    assert ("shopkit/checkout.py:total", "shopkit/tax.py:tax_for", "runtime") in eff
    assert not any(a == "shopkit/checkout.py:total" and src == "static" for a, _, src in eff)
    # to_csv never ran: its static arrow is used
    assert ("shopkit/export.py:to_csv", "shopkit/catalog.py:Catalog.price_of", "static") in eff
    # imports have no runtime twin, so they always count
    assert ("shopkit/checkout.py", "shopkit/money.py", "static") in eff


def test_static_call_from_traced_function_is_dropped(built, tmp_path):
    """A call the tests never took from a function they did run is not an effective arrow (runtime wins)."""
    shop, db, m, calls = built
    p = shop / "src/shopkit/tax.py"
    p.write_text(p.read_text() + "\n\ndef never():\n    return 1\n")
    p.write_text(p.read_text().replace("    return round_cents(", "    if region == 'XX':\n        never()\n"
                                       "    return round_cents("))
    graph.update_files(db, shop / "src/shopkit", ["shopkit/tax.py"])
    static_only = rows(db, "SELECT 1 FROM arrows WHERE src = 'shopkit/tax.py:tax_for' AND dst = "
                           "'shopkit/tax.py:never' AND source = 'static'")
    assert static_only
    eff = {(a, b) for a, b, *_ in graph.arrows(db, level="line")}
    assert ("shopkit/tax.py:tax_for", "shopkit/tax.py:never") not in eff


def test_covers_maps_lines_to_functions(built):
    _, db, _, _ = built
    funcs = {f for (f,) in rows(db, "SELECT function FROM covers WHERE test = "
                                    "'tests/test_money.py::test_round_half_up'")}
    assert "shopkit/money.py:round_cents" in funcs
    assert not any(f.startswith("shopkit/checkout.py") for f in funcs)


def test_call_order_and_values(built):
    _, db, _, _ = built
    steps = graph.test_calls(db, "tests/test_checkout.py::test_receipt")
    by_fn = {s["function"]: s for s in steps}
    receipt, total = by_fn["shopkit/checkout.py:receipt"], by_fn["shopkit/checkout.py:total"]
    assert receipt["depth"] == 1 and receipt["parent_seq"] is None
    assert total["parent_seq"] == receipt["seq"] and total["depth"] == 2
    assert [s["seq"] for s in steps] == list(range(len(steps)))
    assert receipt["args"]["region"] == {"type": "str", "repr": "'NY'"}
    assert receipt["ret"]["repr"].startswith("'book x2")
    assert by_fn["shopkit/cart.py:Cart.subtotal"]["args"]["self"] == {"type": "shopkit.cart.Cart", "repr": None}
    (steps_n, truncated), = rows(db, "SELECT steps, truncated FROM test_runs WHERE test = ?",
                                 "tests/test_checkout.py::test_receipt")
    assert steps_n == len(steps) and truncated == 0


def test_raised_exception_is_recorded(built):
    _, db, _, _ = built
    steps = graph.test_calls(db, "tests/test_catalog.py::test_unknown_product")
    price = [s for s in steps if s["function"] == "shopkit/catalog.py:Catalog.price_of"][-1]
    assert price["raised"] == "KeyError" and price["ret"] is None


def test_rollups(built):
    _, db, _, _ = built
    assert graph.rollup(db, "shopkit/cart.py:Cart.subtotal.<locals>.<genexpr>", "function") == \
        "shopkit/cart.py:Cart.subtotal"
    assert graph.rollup(db, "shopkit/cart.py:Cart.subtotal", "class") == "shopkit/cart.py:Cart"
    assert graph.rollup(db, "shopkit/cart.py:Cart.subtotal", "module") == "shopkit/cart.py"
    mods = {(a, b) for a, b, *_ in graph.arrows(db, level="module")}
    assert ("shopkit/checkout.py", "shopkit/tax.py") in mods and ("shopkit/tax.py", "shopkit/money.py") in mods
    classes = {(a, b) for a, b, *_ in graph.arrows(db, level="class")}
    assert ("shopkit/cart.py:Cart", "shopkit/catalog.py:Catalog") in classes


def test_per_file_update_equals_full_rebuild(built, tmp_path):
    shop, db, m, calls = built
    p = shop / "src/shopkit/export.py"
    p.write_text(p.read_text() + "\n\ndef to_tsv(cart):\n    return to_csv(cart).replace(',', '\\t')\n")
    assert graph.changed_files(db, shop / "src/shopkit") == ["shopkit/export.py"]
    graph.update_files(db, shop / "src/shopkit", graph.changed_files(db, shop / "src/shopkit"))
    fresh = graph.build(shop / "src/shopkit", m, calls, path=tmp_path / "fresh.db")
    for table in ("dots", "arrows", "covers", "test_calls"):
        q = f"SELECT * FROM {table} ORDER BY 1, 2, 3"
        assert rows(db, q) == rows(fresh, q), table
    assert ("shopkit/export.py:to_tsv", "shopkit/export.py:to_csv") in \
        {(a, b) for a, b, *_ in graph.arrows(db, level="line")}
    assert graph.changed_files(db, shop / "src/shopkit") == []


def test_deleted_function_leaves_the_graph(built):
    shop, db, _, _ = built
    p = shop / "src/shopkit/export.py"
    p.write_text("")
    graph.update_files(db, shop / "src/shopkit", ["shopkit/export.py"])
    assert not rows(db, "SELECT 1 FROM dots WHERE key = 'shopkit/export.py:to_csv'")
    assert not rows(db, "SELECT 1 FROM arrows WHERE src = 'shopkit/export.py:to_csv'")


def test_property_getter_and_setter_share_one_key(tmp_path):
    """Both halves of a property have the key C.p; tests that ran either half must cover it."""
    pkg = tmp_path / "src/pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text("class C:\n    @property\n    def p(self):\n        return 1\n\n"
                              "    def other(self):\n        return 2\n\n"
                              "    @p.setter\n    def p(self, v):\n        self.v = v\n")
    m = {"functions": {"pkg/m.py:C.p": {"first": 2}, "pkg/m.py:C.other": {"first": 6}},
         "tests_map": {"t::get": {"outcome": "pass", "lines": ["pkg/m.py:4"]},
                       "t::set": {"outcome": "pass", "lines": ["pkg/m.py:11"]},
                       "t::other": {"outcome": "pass", "lines": ["pkg/m.py:7"]}}}
    db = graph.build(pkg, m, {}, path=tmp_path / "g.db")
    cov = dict(rows(db, "SELECT test, function FROM covers"))
    assert cov == {"t::get": "pkg/m.py:C.p", "t::set": "pkg/m.py:C.p", "t::other": "pkg/m.py:C.other"}
