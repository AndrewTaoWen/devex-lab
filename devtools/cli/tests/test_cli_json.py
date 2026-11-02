import json

from conftest import repomap


def test_status_json(shop):
    s = json.loads(repomap(shop, "status", "--json"))
    assert s["tests"] == 18 and s["changed_files"] == []


def test_impact_of_uncommitted_edit(shop):
    p = shop / "src/shopkit/money.py"
    p.write_text(p.read_text().replace("rounding=ROUND_HALF_UP", 'rounding="ROUND_DOWN"'))
    r = json.loads(repomap(shop, "impact", "--json"))
    assert r["changed"] == ["shopkit/money.py:round_cents"]
    assert r["callers"][0] == ["shopkit/checkout.py:total", "shopkit/tax.py:tax_for"]
    assert len(r["tests"]) == 6


def test_impact_reports_unmatched_names(shop):
    r = json.loads(repomap(shop, "impact", "no_such_function", "--json"))
    assert r["unmatched"] == ["no_such_function"] and r["tests"] == []


def test_query_gaps_and_tests(shop):
    gaps = json.loads(repomap(shop, "query", "gaps", "money", "--json"))
    assert {"function": "shopkit/money.py:round_cents", "never": {"amount": ["negative"]}} in gaps
    tests = json.loads(repomap(shop, "query", "tests", "src/shopkit/money.py:6", "--json"))
    assert "tests/test_money.py::test_round_half_up" in tests


def test_refresh_json(shop):
    p = shop / "src/shopkit/discounts.py"
    p.write_text(p.read_text().replace("item_count >= 10", "item_count > 10"))
    r = json.loads(repomap(shop, "refresh", "--json"))  # every test that runs code in discounts.py
    assert r["retraced"] == 8 and r["changed_files"] == ["src/shopkit/discounts.py"]
    assert json.loads(repomap(shop, "status", "--json"))["changed_files"] == []
