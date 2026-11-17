"""What the map page gets from the code graph: source-only arrows, public functions and the scoring weights."""
import json, os

from conftest import repomap
from repomap import cli


def page_data(repo):
    previous_dir = os.getcwd()
    os.chdir(repo)
    try:
        memory = cli.load()
        return cli.ui_data(memory)
    finally:
        os.chdir(previous_dir)


def index_of(data, key):
    return next(i for i, function in enumerate(data["fns"]) if function[0] == key)


def test_no_graph_no_graph_data(shop):
    """Without a graph file, the page gets no graph data and works as it did before."""
    assert page_data(shop)["graph"] is None


def test_calls_through_a_genexpr_connect_caller_and_callee(shop):
    """Cart.subtotal calls price_of from inside a generator expression. The page must still draw that arrow, by
    folding the generator into Cart.subtotal."""
    data = page_data(shop)
    assert [index_of(data, "shopkit/cart.py:Cart.subtotal"), index_of(data, "shopkit/catalog.py:Catalog.price_of")] in \
        [list(edge) for edge in data["edges"]]


def test_graph_adds_source_only_arrows_and_public(shop):
    """With a graph file, the page gets the arrows read from the source for code no test runs (to_csv to
    price_of), but never for code the tests ran. It also gets the public functions and the scoring weights."""
    repomap(shop, "graph", "update")
    data = page_data(shop)
    graph_data = data["graph"]
    to_csv, price_of = index_of(data, "shopkit/export.py:to_csv"), index_of(data, "shopkit/catalog.py:Catalog.price_of")
    assert [to_csv, price_of, "calls_by_name"] in [list(edge) for edge in graph_data["sedges"]]
    # arrows from traced functions come from the trace only, never from the source layer
    traced_src = {index for index, function in enumerate(data["fns"]) if function[3]}
    assert not any(src in traced_src for src, _, _ in graph_data["sedges"])
    assert index_of(data, "shopkit/checkout.py:receipt") in graph_data["public"]
    assert (graph_data["step"], graph_data["static"], graph_data["guess"], graph_data["cutoff"]) == (0.9, 0.5, 0.8, 0.3)


def test_exported_page_carries_the_graph(shop, tmp_path):
    """A page saved with 'serve --export' carries the graph data and the legend for it, so it works as a single
    file."""
    repomap(shop, "graph", "update")
    out = tmp_path / "map.html"
    repomap(shop, "serve", "--export", str(out))
    html = out.read_text()
    assert '"sedges":' in html and "seen only in source" in html
