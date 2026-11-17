"""The readable Graph tab: layout and numbers for every function, stable positions, the routes that serve them,
and a page that loads cleanly."""
import json, os, shutil, socket, subprocess, sys, time, urllib.request
from pathlib import Path

import pytest

from conftest import CLI, git, repomap
from repomap import cli, graph, graphmetrics, readable

PAGE_CHECK = Path(__file__).with_name("page_check.js")


def payload(repo, **kwargs):
    previous_dir = os.getcwd()
    os.chdir(repo)
    try:
        return readable.payload(graph.connect(), graphmetrics.load_memory(), **kwargs)
    finally:
        os.chdir(previous_dir)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(shop):
    repomap(shop, "graph", "update")
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "repomap", "serve", "--port", str(port)], cwd=shop,
                            env={"PYTHONPATH": str(CLI / "src"), "PATH": "/usr/bin:/bin"},
                            stdout=subprocess.PIPE, text=True)
    url = proc.stdout.readline().split(": ", 1)[1].split()[0]
    yield url
    proc.terminate()
    proc.wait()


def get(url):
    with urllib.request.urlopen(url, timeout=30) as res:
        return res.read()


def test_every_function_has_a_place_and_numbers(shop):
    """All 24 functions and module blocks get a cell inside the drawing, with their numbers; nested code (the genexpr
    in Cart.subtotal) has no cell of its own. Landmarks and groups name real functions."""
    repomap(shop, "graph", "update")
    data = payload(shop)
    assert len(data["units"]) == 24 and not any("<locals>" in key for key in data["units"])
    for unit in data["units"].values():
        x, y, w, h = unit["xy"]
        assert 0 <= x and 0 <= y and x + w <= data["W"] + 0.01 and y + h <= data["H"] + 0.01 and w > 0 and h > 0
        assert len(unit["n"]) == len(data["fields"])
    assert set(data["landmarks"]) <= set(data["units"])
    assert {f["name"] for f in data["files"]} >= {"shopkit/cart.py", "shopkit/checkout.py"}


def test_positions_stay_when_another_file_changes(shop):
    """Editing round_cents' body (no functions added or removed) moves nothing on the map."""
    repomap(shop, "graph", "update")
    before = {key: unit["xy"] for key, unit in payload(shop)["units"].items()}
    path = shop / "src/shopkit/money.py"
    path.write_text(path.read_text().replace("    return float(Decimal", "    # rounding\n    return float(Decimal"))
    git(shop, "commit", "-qam", "edit")
    repomap(shop, "graph", "update")
    after = payload(shop)
    assert {key: unit["xy"] for key, unit in after["units"].items()} == before
    assert after["units"]["shopkit/money.py:round_cents"]["n"][after["fields"].index("changes")] == 2


def test_routes_serve_the_numbers_and_the_library(server):
    """/api/readable returns the payload (any git window, or all history) and /static/d3.min.js serves the bundled
    library, so the page works offline."""
    data = json.loads(get(server + "/api/readable"))
    assert data["since"] == "12 months ago" and len(data["units"]) == 24
    assert json.loads(get(server + "/api/readable?since=all"))["since"] == "all history"
    assert get(server + "/static/d3.min.js").startswith(b"// https://d3js.org v7")


def test_export_inlines_the_library_and_the_numbers(shop, tmp_path):
    """A page saved with 'serve --export' carries d3 and the readable data inline, with no script to fetch."""
    repomap(shop, "graph", "update")
    out = tmp_path / "map.html"
    repomap(shop, "serve", "--export", str(out))
    html = out.read_text()
    assert cli.D3_TAG not in html and "d3js.org v7" in html
    assert '"units":' in html and '"landmarks":' in html


@pytest.mark.skipif(not shutil.which("node"), reason="needs node with playwright")
def test_page_draws_the_overview_without_errors(server):
    """In a headless browser, the Graph tab's overview draws one cell per function and logs no errors."""
    res = subprocess.run(["node", str(PAGE_CHECK), server, "overview"], capture_output=True, text=True, timeout=120)
    if "Cannot find module 'playwright'" in res.stderr:
        pytest.skip("playwright is not installed")
    assert res.returncode == 0, res.stderr
    report = json.loads(res.stdout)
    assert report["errors"] == [] and report["cells"] == 24 and report["landmarks"] >= 1
