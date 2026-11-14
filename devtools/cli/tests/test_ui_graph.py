"""What the map page gets from the code graph: source-only arrows, public functions and the scoring weights."""
import json, os

from conftest import repomap
from repomap import cli


def page_data(repo):
    old = os.getcwd()
    os.chdir(repo)
    try:
        m = cli.load()
        return cli.ui_data(m)
    finally:
        os.chdir(old)


def idx(d, key):
    return next(i for i, f in enumerate(d["fns"]) if f[0] == key)


def test_no_graph_no_graph_data(shop):
    assert page_data(shop)["graph"] is None


def test_calls_through_a_genexpr_connect_caller_and_callee(shop):
    # Cart.subtotal calls price_of inside a generator expression; the page must still draw that arrow
    d = page_data(shop)
    assert [idx(d, "shopkit/cart.py:Cart.subtotal"), idx(d, "shopkit/catalog.py:Catalog.price_of")] in \
        [list(e) for e in d["edges"]]


def test_graph_adds_source_only_arrows_and_public(shop):
    repomap(shop, "graph", "update")
    d = page_data(shop)
    g = d["graph"]
    assert [idx(d, "shopkit/export.py:to_csv"), idx(d, "shopkit/catalog.py:Catalog.price_of"), "calls_by_name"] in \
        [list(e) for e in g["sedges"]]
    # arrows from traced functions come from the trace only, never from the source layer
    traced_src = {i for i, f in enumerate(d["fns"]) if f[3]}
    assert not any(a in traced_src for a, _, _ in g["sedges"])
    assert idx(d, "shopkit/checkout.py:receipt") in g["public"]
    assert (g["step"], g["static"], g["guess"], g["cutoff"]) == (0.9, 0.5, 0.8, 0.3)


def test_exported_page_carries_the_graph(shop, tmp_path):
    repomap(shop, "graph", "update")
    out = tmp_path / "map.html"
    repomap(shop, "serve", "--export", str(out))
    html = out.read_text()
    assert '"sedges":' in html and "seen only in source" in html
