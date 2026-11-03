import json
import subprocess
from pathlib import Path

import pytest

from futurebench import Parts, Reward, Weights, list_tasks
from futurebench import build_tasks, env
from futurebench.reward import bug_credit
from futurebench.reward import test_function as strip_params

EXPERIMENTS = Path(__file__).resolve().parents[2] / "experiments"


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


def test_shipped_tasks_match_the_experiments():
    tasks = {t.id: t for t in list_tasks()}
    assert len(tasks) == 12
    train = [t for t in tasks.values() if t.split == "train"]
    evals = [t for t in tasks.values() if t.split == "eval"]
    # 116 distinct bugs in 03-analysis, 30 in 04-ab-test
    assert sum(len(t.bug_units) for t in train) == 116
    assert sum(len(t.bug_units) for t in evals) == 30
    for t in tasks.values():
        tests = [x for u in t.units for x in u["tests"]]
        assert len(tests) == len(set(tests)), t.id


@pytest.mark.skipif(not EXPERIMENTS.exists(), reason="experiments data not present")
def test_manifests_are_reproducible_from_experiments(tmp_path):
    build_tasks.main([str(EXPERIMENTS), str(tmp_path)])
    shipped = {t.id: t for t in list_tasks()}
    for f in tmp_path.glob("*.json"):
        rebuilt = json.loads(f.read_text())
        assert rebuilt["units"] == shipped[rebuilt["id"]].units


def test_prompt_fills_every_field():
    t = env.get_task("tinydb")
    for variant in ("improved", "baseline"):
        text = env.prompt(t, variant, known_failing=["tests/x.py::test_a"])
        assert "{" not in text.replace("{}", "")
        assert "/workspace" in text and "msiemens/tinydb" in text and "test_a" in text


def _repo(tmp_path):
    repo = tmp_path / "ws"
    repo.mkdir()
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a.py").write_text("x = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a(): pass\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=s", "-c", "user.email=s@s", "commit", "-qm", "s"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    snap = env.git(repo, "rev-parse", "HEAD").strip()
    (repo / ".git" / "futurebench.json").write_text(json.dumps({"task": "tinydb", "snapshot_commit": snap}))
    return repo, snap


def test_workspace_tree_sees_untracked_and_deleted_files(tmp_path):
    repo, snap = _repo(tmp_path)
    (repo / "pkg" / "new.py").write_text("y = 2\n")
    (repo / "tests" / "test_a.py").unlink()
    _, base, tree = env.workspace_tree(repo)
    assert base == snap
    names = env.git(repo, "diff", "--name-status", base, tree)
    assert "A\tpkg/new.py" in names and "D\ttests/test_a.py" in names
    # the real index is untouched
    assert env.git(repo, "status", "--porcelain").count("??") == 1


def test_replace_paths_swaps_and_removes(tmp_path):
    repo, snap = _repo(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "tests").mkdir()
    (dst / "tests" / "other.py").write_text("")
    (dst / "gone").mkdir()
    env.replace_paths(dst, repo, snap, ["tests", "gone"])
    assert (dst / "tests" / "test_a.py").exists() and not (dst / "tests" / "other.py").exists()
    assert not (dst / "gone").exists()
