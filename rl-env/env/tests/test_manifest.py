import json
from pathlib import Path

import pytest

from futurebench import build_tasks
from futurebench.manifest import get_task, list_tasks

EXPERIMENTS = Path(__file__).resolve().parents[2] / "experiments"


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


def test_get_task_and_split_filter():
    assert get_task("tinydb").repo == "msiemens/tinydb"
    assert {t.id for t in list_tasks("eval")} == {"python-dotenv", "cattrs", "werkzeug", "pydantic-settings"}
    with pytest.raises(KeyError):
        get_task("nope")
