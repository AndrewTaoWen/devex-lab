"""futurebench command line.

  futurebench list [--split train|eval]
  futurebench build TASK... [--rebuild]          build the agent and score images
  futurebench prepare TASK WORKSPACE [--prompt improved|baseline] [--no-start]
  futurebench exec WORKSPACE -- CMD...           run a command in the run's sandbox
  futurebench stop WORKSPACE                     remove the run's container
  futurebench score WORKSPACE [--out FILE] [--no-replay]
  futurebench rescore TASK PATCH... [--out FILE] apply old patches to fresh workspaces and score them
  futurebench reweigh FILE [--tests 0.25 ...]    recompute totals from stored parts with new weights
"""
import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

from . import env, sandbox
from .reward import Reward, Weights


def _weights(a):
    kw = {f.name: getattr(a, f"w_{f.name}") for f in fields(Weights) if getattr(a, f"w_{f.name}", None) is not None}
    return Weights(**kw)


def _add_weights(p):
    for f in fields(Weights):
        p.add_argument(f"--{f.name.replace('_', '-')}", dest=f"w_{f.name}", type=type(f.default), default=None)


def _container_name(ws: Path):
    meta = json.loads((ws / ".git" / "futurebench.json").read_text())
    return f"fb-{meta['task']}-{ws.resolve().name}"


def _save(out, key, reward):
    if not out:
        return
    data = json.loads(out.read_text()) if out.exists() else {}
    data[key] = reward.to_dict()
    out.write_text(json.dumps(data, indent=1) + "\n")


def _summary(key, r):
    p = r.parts
    return (f"{key}: R={r.total:.3f}  bugs {p.bugs_fixed:g}/{p.bugs_available}  verified tests {p.verified_tests}  "
            f"regressions {p.regressions}  collisions {p.collisions} over {p.changed_lines} lines  "
            f"(target tests fixed {len(p.detail['target_tests_fixed'])}/{p.detail['target_tests']})")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="futurebench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--split")
    p = sub.add_parser("build")
    p.add_argument("tasks", nargs="+")
    p.add_argument("--rebuild", action="store_true")
    p = sub.add_parser("prepare")
    p.add_argument("task")
    p.add_argument("workspace", type=Path)
    p.add_argument("--prompt", default="improved", choices=["improved", "baseline"])
    p.add_argument("--no-start", action="store_true")
    p = sub.add_parser("exec")
    p.add_argument("workspace", type=Path)
    p.add_argument("command", nargs=argparse.REMAINDER)
    p = sub.add_parser("stop")
    p.add_argument("workspace", type=Path)
    p = sub.add_parser("score")
    p.add_argument("workspace", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--no-replay", action="store_true")
    _add_weights(p)
    p = sub.add_parser("rescore")
    p.add_argument("task")
    p.add_argument("patches", nargs="+", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--no-replay", action="store_true")
    p.add_argument("--keep", action="store_true", help="keep the workspaces")
    _add_weights(p)
    p = sub.add_parser("reweigh")
    p.add_argument("file", type=Path)
    _add_weights(p)
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for t in env.list_tasks(a.split):
            print(f"{t.id:20} {t.split:5} {t.repo:30} bugs {len(t.bug_units):3}  "
                  f"units {len(t.units):3}  snapshot {t.snapshot_date}  head {t.head_date}")
    elif a.cmd == "build":
        for tid in a.tasks:
            env.ensure_images(env.get_task(tid), rebuild=a.rebuild)
    elif a.cmd == "prepare":
        run = env.prepare(a.task, a.workspace, variant=a.prompt, start=not a.no_start)
        (run.workspace / ".git" / "futurebench-prompt.txt").write_text(run.prompt)
        print(f"workspace {run.workspace}" + (f"\ncontainer {run.container.name}" if run.container else ""))
        print(f"prompt in {run.workspace / '.git' / 'futurebench-prompt.txt'}")
    elif a.cmd == "exec":
        cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
        sys.exit(subprocess.run(["docker", "exec", "-w", sandbox.MOUNTS["agent"], _container_name(a.workspace), *cmd]).returncode)
    elif a.cmd == "stop":
        sandbox.docker("rm", "-f", _container_name(a.workspace), check=False)
    elif a.cmd == "score":
        r = env.score(a.workspace, _weights(a), replay=not a.no_replay)
        _save(a.out, str(a.workspace), r)
        print(_summary(a.workspace.name, r))
        if not a.out:
            print(json.dumps(r.to_dict(), indent=1))
    elif a.cmd == "rescore":
        task = env.get_task(a.task)
        for patch in a.patches:
            run = env.prepare(task, start=False, log=lambda *_: None)
            try:
                p = subprocess.run(["git", "-C", str(run.workspace), "apply", "--whitespace=nowarn", str(patch.resolve())],
                                   capture_output=True, text=True)
                if p.returncode:
                    print(f"{patch.stem}: patch does not apply: {p.stderr[:300]}")
                    continue
                r = env.score(run.workspace, _weights(a), replay=not a.no_replay, log=lambda *_: None)
                _save(a.out, patch.stem, r)
                print(_summary(patch.stem, r), flush=True)
            finally:
                if not a.keep:
                    shutil.rmtree(run.workspace, ignore_errors=True)
    elif a.cmd == "reweigh":
        data = json.loads(a.file.read_text())
        for k, d in data.items():
            base = Weights(**d["weights"])
            over = {f.name: getattr(a, f"w_{f.name}") for f in fields(Weights) if getattr(a, f"w_{f.name}") is not None}
            if "bug_credit" in over and over["bug_credit"] != base.bug_credit:
                sys.exit("bug_credit changes need a rescore (it decides which units count); use rescore")
            r = Reward.from_dict(d, Weights(**{**d["weights"], **over}))
            print(_summary(k, r))


if __name__ == "__main__":
    main()
