"""The CLI runs as `python -m repomap` from the src/ layout, and the checkout shim still works."""
import subprocess, sys

from conftest import CLI, repomap


def test_status_after_record(shop):
    out = repomap(shop, "status")
    assert "Memory: 18 tests" in out and "Library files changed since: 0" in out


def test_impact_of_uncommitted_edit(shop):
    p = shop / "src/shopkit/money.py"
    p.write_text(p.read_text().replace("rounding=ROUND_HALF_UP", 'rounding="ROUND_DOWN"'))
    out = repomap(shop, "impact")
    assert "shopkit/money.py:round_cents  (6 tests)" in out
    assert "run `repomap refresh` to update" in out


def test_checkout_shim_runs_the_package(shop):
    out = subprocess.run([sys.executable, str(CLI / "repomap.py"), "status"], cwd=shop, capture_output=True,
                         text=True, check=True).stdout
    assert "Memory: 18 tests" in out


def test_export_finds_the_packaged_page(shop, tmp_path):
    out = tmp_path / "map.html"
    repomap(shop, "serve", "--export", str(out))
    assert "<html" in out.read_text()[:500].lower()
