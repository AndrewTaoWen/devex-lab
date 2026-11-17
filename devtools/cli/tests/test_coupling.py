"""Coupling groups: every function in exactly one group, the same graph gives the same groups, and functions that
call each other end up together."""
import json

from conftest import repomap
from repomap import coupling


def test_every_function_in_one_group_and_stable(shop):
    repomap(shop, "graph", "update")
    first = json.loads(repomap(shop, "graph", "groups", "--json"))
    second = json.loads(repomap(shop, "graph", "groups", "--json"))
    assert first == second
    members = [key for group in first for key in group["members"]]
    assert len(members) == len(set(members)) == 24


def test_callers_and_callees_group_together(shop):
    """total calls bulk_discount, apply_coupon and tax_for, so they share a group. Module code nobody calls stays
    with its own file."""
    repomap(shop, "graph", "update")
    groups = json.loads(repomap(shop, "graph", "groups", "--json"))
    group_of = {key: group["id"] for group in groups for key in group["members"]}
    total = group_of["shopkit/checkout.py:total"]
    assert {group_of[key] for key in ("shopkit/discounts.py:bulk_discount", "shopkit/discounts.py:apply_coupon",
                                      "shopkit/tax.py:tax_for")} == {total}
    alone = next(group for group in groups if group["members"] == ["shopkit/tax.py:<module>"])
    assert alone["name"] == "shopkit/tax.py"


def test_merge_is_order_independent():
    """Two triangles joined by one arrow split into the two triangles, whatever order the arrows come in."""
    arrows = [("a", "b"), ("b", "c"), ("a", "c"), ("x", "y"), ("y", "z"), ("x", "z"), ("c", "x")]
    forward = coupling.merge({arrow: 1.0 for arrow in arrows})
    backward = coupling.merge({arrow: 1.0 for arrow in reversed(arrows)})
    assert forward == backward
    assert len({forward[n] for n in "abc"}) == 1 and len({forward[n] for n in "xyz"}) == 1
    assert forward["a"] != forward["x"]
