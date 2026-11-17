"""Landmarks: ranked by reach so pipelines beat helpers, with a config file that wins over the ranking."""
import json

from conftest import repomap
from repomap import landmarks


def row(key, reach, tests=1, from_tests=0, kind="function"):
    return dict(key=key, kind=kind, reach=reach, tests=tests, references_from_tests=from_tests)


def test_pipeline_outranks_a_helper_with_more_callers(shop):
    """round_cents has more references than receipt but runs nothing underneath, so receipt is a landmark and
    round_cents is not."""
    repomap(shop, "graph", "update")
    picked = [row["key"] for row in json.loads(repomap(shop, "graph", "landmarks", "--json"))]
    assert picked[:2] == ["shopkit/checkout.py:receipt", "shopkit/checkout.py:total"]
    assert "shopkit/money.py:round_cents" not in picked
    assert len(picked) == 5


def test_entry_points_get_a_boost():
    """At equal reach, a function tests call directly ranks above an internal one."""
    rows = [row("pkg/a.py:_inner", 10), row("pkg/b.py:_entry", 10, from_tests=3)] + \
        [row(f"pkg/c.py:_f{i}", 1) for i in range(6)]
    assert [r["key"] for r in landmarks.pick(rows)][:2] == ["pkg/b.py:_entry", "pkg/a.py:_inner"]


def test_config_pins_adds_and_removes(shop):
    """The override file wins: a pinned function comes first, an added one joins even with no reach, a removed
    one never shows, and count sets how many."""
    repomap(shop, "graph", "update")
    (shop / ".repomap/landmarks.toml").write_text(
        'count = 3\npin = ["shopkit/money.py:round_cents"]\nadd = ["shopkit/export.py:to_csv"]\n'
        'remove = ["shopkit/checkout.py:receipt"]\n')
    picked = json.loads(repomap(shop, "graph", "landmarks", "--json"))
    assert [(row["key"], row["why"]) for row in picked] == [
        ("shopkit/money.py:round_cents", "pinned"), ("shopkit/checkout.py:total", "reach"),
        ("shopkit/export.py:to_csv", "added")]
