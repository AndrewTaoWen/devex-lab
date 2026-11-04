"""Task manifests: one frozen JSON per repo in tasks/, built by build_tasks.py."""
import json
from dataclasses import dataclass, field
from importlib import resources


@dataclass
class Task:
    id: str
    repo: str
    split: str
    snapshot: str
    snapshot_date: str
    cutoff: str
    head: str
    head_date: str
    packages: list
    tests: str
    units: list
    extra_deps: list = field(default_factory=list)
    pytest_args: list = field(default_factory=list)

    @property
    def bug_units(self):
        return [u for u in self.units if u["label"] == "bug"]


def list_tasks(split=None):
    files = sorted(resources.files("futurebench").joinpath("tasks").iterdir(), key=lambda p: p.name)
    tasks = [Task(**json.loads(f.read_text())) for f in files if f.name.endswith(".json")]
    return [t for t in tasks if split in (None, t.split)]


def get_task(task_id):
    for t in list_tasks():
        if t.id == task_id:
            return t
    raise KeyError(f"unknown task {task_id!r}; known: {', '.join(t.id for t in list_tasks())}")
