import json

import pytest

from futurebench.reward import Parts, Reward, Weights, bug_credit
from futurebench.reward import test_function as strip_params


def parts(**kw):
    base = dict(bugs_available=8, bugs_fixed=3, verified_tests=30, regressions=1, collisions=2, changed_lines=100)
    return Parts(**{**base, **kw})


def test_worked_example_from_plan():
    r = Reward.from_parts(parts())
    assert r.terms == {"bugs": 0.375, "tests": 0.25, "regressions": -0.125, "collisions": -0.2}
    assert r.total == pytest.approx(0.3)


def test_test_term_is_capped_and_zero_without_tests():
    assert Reward.from_parts(parts(verified_tests=500)).terms["tests"] == 0.25
    assert Reward.from_parts(parts(verified_tests=10)).terms["tests"] == 0.125
    assert Reward.from_parts(parts(verified_tests=0)).terms["tests"] == 0


def test_no_bugs_available_does_not_divide_by_zero():
    r = Reward.from_parts(parts(bugs_available=0, bugs_fixed=0, regressions=0))
    assert r.terms["bugs"] == 0


def test_no_changes_means_no_collision_penalty():
    assert Reward.from_parts(parts(collisions=0, changed_lines=0)).terms["collisions"] == 0


def test_reweigh_round_trip():
    r = Reward.from_parts(parts())
    again = Reward.from_dict(json.loads(json.dumps(r.to_dict())), Weights(collisions=0))
    assert again.terms["collisions"] == 0
    assert again.total == pytest.approx(0.5)


def test_bug_credit_modes():
    units = [{"tests": ["a", "b"]}, {"tests": ["c"]}]
    assert bug_credit(units, {"a", "c"}, "all") == 1
    assert bug_credit(units, {"a", "c"}, "any") == 2
    assert bug_credit(units, {"a", "c"}, "fraction") == 1.5
    with pytest.raises(ValueError):
        bug_credit(units, set(), "most")


def test_test_function_strips_parametrization():
    assert strip_params("tests.t::test_x[a-b[c]]") == "tests.t::test_x"
    assert strip_params("tests.t::C::test_y") == "tests.t::C::test_y"
