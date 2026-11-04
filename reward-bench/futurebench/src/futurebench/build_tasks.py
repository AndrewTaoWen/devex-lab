"""Freeze the experiments' screen + label files into one task manifest per repo.

usage: python -m futurebench.build_tasks EXPERIMENTS_DIR [OUT_DIR]

Reads 02-scale-up/{screen,labels.json,label-overrides.json}, 04-ab-test/{screen,labels-heldout.json}
and 03-analysis/bug-catch.json. A task's target tests are grouped into "units": the commit that added
them when label.py found one (or the hand-found commits from 04-ab-test/ab.py), else the test itself.
This reproduces the 116 distinct bug units of 03-analysis on the train split and the 30 of 04-ab-test
on the eval split.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

TRAIN = ["attrs", "cachetools", "marshmallow", "more-itertools", "pluggy", "sqlparse", "tinydb", "tomlkit"]
EVAL = ["python-dotenv", "cattrs", "werkzeug", "pydantic-settings"]

# adding commits found by hand (git log -S on the parameter text), copied from 04-ab-test/ab.py
FOUND = {"python-dotenv": [("#", "f5485a6"), ("\\\\", "f7b18d9")],
         "werkzeug": [("test_get_host_invalid", "658bf5ed"), ("test_plain_int", "b9761b5b"),
                      ("test_if_range_parsing", "5b0dc6d6"), ("test_range_parsing", "0c419f95"),
                      ("test_csp_header", "63a800ca")]}


# per-task pytest arguments. werkzeug: the dev-server tests start live servers; with watchdog installed
# (its optional extra) the reloader test never returns inside the sandbox, and no target test needs them.
PYTEST_ARGS = {"werkzeug": ["-m", "not dev_server"]}


def final_label(overrides, row):
    """Same precedence as 04-ab-test/ab.py: hand overrides by test or commit prefix, then the auto label."""
    func = re.sub(r"\[.*", "", row["test"].split("::")[-1])
    if not row["commit"] and "test:" + func in overrides:
        return overrides["test:" + func]
    if not row["commit"] and "test:*" in overrides:
        return overrides["test:*"]
    for k, v in overrides.items():
        if row["commit"] and not k.startswith(("test:", "*", "_")) and row["commit"].startswith(k):
            return v
    return overrides.get("*", row["label"])


def unit_id(name, row):
    if row["commit"]:
        return row["commit"]
    for pat, c in FOUND.get(name, []):
        if pat in row["test"]:
            return c
    return "test:" + row["test"].split("::")[-1]


def build(exp: Path):
    overrides = json.loads((exp / "02-scale-up/label-overrides.json").read_text())
    labels = {**json.loads((exp / "02-scale-up/labels.json").read_text()),
              **json.loads((exp / "04-ab-test/labels-heldout.json").read_text())}
    kinds = {(b["repo"], b["bug"]): b["kind"] for b in json.loads((exp / "03-analysis/bug-catch.json").read_text())}
    tasks = {}
    for split, names, screen_dir in (("train", TRAIN, exp / "02-scale-up/screen"),
                                     ("eval", EVAL, exp / "04-ab-test/screen")):
        for name in names:
            scr = json.loads((screen_dir / f"{name}.json").read_text())
            rows = {r["test"]: r for r in labels[name]}
            target = scr["future_tests_failing_on_snapshot"]
            missing = [t for t in target if t not in rows]
            if missing:
                raise SystemExit(f"{name}: {len(missing)} target tests have no label")
            units = defaultdict(lambda: {"tests": []})
            for t in target:
                r = rows[t]
                u = units[unit_id(name, r)]
                u["tests"].append(t)
                u.setdefault("label", final_label(overrides.get(name, {}), r))
                u.setdefault("subject", r.get("subject", ""))
            out_units = []
            for uid, u in sorted(units.items()):
                entry = {"id": uid, "label": u["label"], "subject": u["subject"], "tests": sorted(u["tests"])}
                if (name, uid) in kinds:
                    entry["kind"] = kinds[(name, uid)]
                out_units.append(entry)
            tasks[name] = {
                "id": name,
                "repo": scr["repo"],
                "split": split,
                "snapshot": scr["snapshot"],
                "snapshot_date": scr["snapshot_date"],
                "cutoff": "2026-06-01",
                "head": scr["head"],
                "head_date": scr["head_date"],
                "packages": scr["packages"],
                "tests": scr["tests"],
                "units": out_units,
            }
            if name in PYTEST_ARGS:
                tasks[name]["pytest_args"] = PYTEST_ARGS[name]
    return tasks


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    exp = Path(argv[0])
    out = Path(argv[1]) if len(argv) > 1 else Path(__file__).parent / "tasks"
    out.mkdir(parents=True, exist_ok=True)
    for name, task in build(exp).items():
        (out / f"{name}.json").write_text(json.dumps(task, indent=1) + "\n")
        n = defaultdict(int)
        for u in task["units"]:
            n[u["label"]] += 1
        print(f"{name} ({task['split']}): {dict(n)} units, {sum(len(u['tests']) for u in task['units'])} target tests")


if __name__ == "__main__":
    main()
