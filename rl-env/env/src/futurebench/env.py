"""The environment: reset(task) -> Episode, score(episode or workspace) -> Reward."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from importlib import resources

import defusedxml.ElementTree as ET

from . import sandbox
from .replay import collisions
from .manifest import Task, get_task, list_tasks  # noqa: F401
from .reward import Parts, Reward, Weights, bug_credit, test_function

HOME = Path(os.environ.get("FUTUREBENCH_HOME", Path.home() / ".cache" / "futurebench"))
PYTEST = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", "-o", "addopts=",
          "--continue-on-collection-errors", "--junitxml=.fb-junit.xml"]
IGNORE = ".venv/\n__pycache__/\n*.pyc\n*.egg-info/\n.fb-junit.xml\n.fb/\n.pytest_cache/\n"


# ---------------------------------------------------------------- images

def agent_image(task):
    return f"futurebench/{task.id}:agent-{task.snapshot[:10]}-{sandbox.base_tag()}"


def score_image(task):
    return f"futurebench/{task.id}:score-{task.head[:10]}-{sandbox.base_tag()}"


def prompt(task, variant="improved", known_failing=()):
    text = resources.files("futurebench").joinpath("prompts", f"{variant}.txt").read_text()
    kf = ""
    if known_failing:
        kf = (" These already fail on the untouched snapshot; ignore them: "
              + ", ".join(sorted(known_failing)[:20]) + ("" if len(known_failing) <= 20 else ", ..."))
    return text.format(workdir=sandbox.MOUNTS["agent"], repo=task.repo, snapshot_date=task.snapshot_date,
                       venv=sandbox.VENV, tests=task.tests, known_failing=kf,
                       test_cmd=f"python -m pytest -q -p no:cacheprovider -o addopts= {task.tests}")


# ---------------------------------------------------------------- helpers

def git(repo, *args, env=None, check=True, input=None):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env={**os.environ, **(env or {})}, input=input)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args[:3])}: {p.stderr[-1000:]}")
    return p.stdout


def archive(repo, rev, dst: Path, *paths):
    dst.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as f:
        subprocess.run(["git", "-C", str(repo), "archive", rev, "--", *paths], stdout=f, check=True)
        f.seek(0)
        subprocess.run(["tar", "-x", "-C", str(dst)], stdin=f, check=True)


def replace_paths(dst: Path, repo, rev, paths):
    """Make each of `paths` in dst equal to its content at repo:rev (removed if absent there)."""
    for p in paths:
        target = dst / p
        shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink(missing_ok=True)
        if git(repo, "ls-tree", rev, "--", str(p)).strip():
            archive(repo, rev, dst, str(p))


def junit(path: Path):
    res = {}
    if not path.exists():
        return res
    for tc in ET.parse(path).getroot().iter("testcase"):
        kids = {c.tag for c in tc}
        res[f"{tc.get('classname')}::{tc.get('name')}"] = (
            "fail" if kids & {"failure", "error"} else "skip" if "skipped" in kids else "pass")
    return res


def run_tests(image, kind, tree: Path, tests: str, extra=()):
    (tree / ".fb-junit.xml").unlink(missing_ok=True)
    code, out = sandbox.run_in(image, kind, tree, [*PYTEST, *extra, tests])
    res = junit(tree / ".fb-junit.xml")
    if not res:
        raise RuntimeError(f"no test results from {tree} ({kind}); pytest said:\n{out[-2000:]}")
    return res


def clone(task) -> Path:
    path = HOME / "clones" / task.repo.replace("/", "_")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout",
                        f"https://github.com/{task.repo}", str(path)], check=True)
    if git(path, "cat-file", "-t", task.head, check=False).strip() != "commit":
        subprocess.run(["git", "-C", str(path), "fetch", "-q", "origin"], check=True)
    return path


def ensure_images(task, rebuild=False, log=print):
    repo = clone(task)
    for kind, rev, tag in (("agent", task.snapshot, agent_image(task)), ("score", task.head, score_image(task))):
        if rebuild or not sandbox.image_exists(tag):
            shutil.rmtree(HOME / "baselines" / task.id, ignore_errors=True)  # baselines depend on the images
            tree = sandbox.temp_dir("fb-tree-")
            try:
                archive(repo, rev, tree)
                sandbox.build_image(tag, tree, kind, task.extra_deps, log=log)
            finally:
                shutil.rmtree(tree, ignore_errors=True)
    return repo


def _cache(task, name, compute):
    path = HOME / "baselines" / task.id / f"{name}-{task.snapshot[:10]}-{task.head[:10]}-{sandbox.base_tag()}.json"
    if path.exists():
        return json.loads(path.read_text())
    res = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=0))
    return res


def baselines(task, repo):
    """Snapshot suite on snapshot code, and future suite on snapshot code (control for regressions)."""
    def snap():
        tree = sandbox.temp_dir()
        try:
            archive(repo, task.snapshot, tree)
            return run_tests(agent_image(task), "agent", tree, task.tests, task.pytest_args)
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    def future_on_snapshot():
        tree = sandbox.temp_dir()
        try:
            archive(repo, task.head, tree)
            replace_paths(tree, repo, task.snapshot, task.packages)
            return run_tests(score_image(task), "score", tree, task.tests, task.pytest_args)
        finally:
            shutil.rmtree(tree, ignore_errors=True)
    return _cache(task, "snapshot", snap), _cache(task, "future-on-snapshot", future_on_snapshot)


# ---------------------------------------------------------------- episodes

@dataclass
class Episode:
    task: Task
    workspace: Path
    container: sandbox.Container | None
    prompt: str

    def exec(self, command: str, timeout=1800):
        if not self.container:
            raise RuntimeError("episode has no container (reset with start=True)")
        return self.container.exec(command, timeout=timeout)

    def close(self):
        if self.container:
            self.container.stop()


def reset(task_id, workspace: Path | None = None, variant="improved", start=True, log=print) -> Episode:
    """Build the task's images if needed, lay out a fresh workspace, and start its sandbox."""
    task = get_task(task_id) if isinstance(task_id, str) else task_id
    repo = ensure_images(task, log=log)
    snap_res, _ = baselines(task, repo)
    ws = Path(workspace) if workspace else Path(tempfile.mkdtemp(prefix=f"fb-{task.id}-", dir=os.environ.get("FUTUREBENCH_TMP")))
    if ws.exists() and any(ws.iterdir()):
        raise FileExistsError(f"workspace {ws} is not empty")
    archive(repo, task.snapshot, ws)
    sandbox.run_in(agent_image(task), "agent", ws, ["true"])  # drop in install-generated files
    (ws / ".gitignore").write_text(((ws / ".gitignore").read_text() if (ws / ".gitignore").exists() else "") + "\n" + IGNORE)
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "-c", "user.name=snapshot", "-c", "user.email=s@s", "commit", "-qm", f"{task.repo} at {task.snapshot[:10]}")
    (ws / ".git" / "futurebench.json").write_text(json.dumps({"task": task.id, "snapshot_commit": git(ws, "rev-parse", "HEAD").strip()}))
    known = [t for t, s in snap_res.items() if s == "fail"]
    container = sandbox.Container(agent_image(task), ws, f"fb-{task.id}-{ws.name}").start() if start else None
    return Episode(task, ws, container, prompt(task, variant, known))


def workspace_tree(ws: Path):
    """(snapshot commit, tree of the workspace as it stands now) inside the workspace's own git repo."""
    meta = json.loads((ws / ".git" / "futurebench.json").read_text())
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": f"{td}/index"}
        git(ws, "read-tree", meta["snapshot_commit"], env=env)
        git(ws, "add", "-A", env=env)
        tree = git(ws, "write-tree", env=env).strip()
    return meta, meta["snapshot_commit"], tree


def score(target, weights: Weights = Weights(), replay=True, log=print) -> Reward:
    """Score an Episode or a workspace path. Runs outside the episode's sandbox, on copies."""
    ws = Path(target.workspace if isinstance(target, Episode) else target)
    meta, base, tree = workspace_tree(ws)
    task = get_task(meta["task"])
    repo = ensure_images(task, log=log)
    snap_res, control = baselines(task, repo)
    numstat = git(ws, "diff", "--numstat", base, tree)
    changed = sum(int(a) + int(b) for a, b, _ in (l.split("\t", 2) for l in numstat.splitlines()) if a != "-")
    files = [l.split("\t", 2)[2] for l in numstat.splitlines()]

    tmp = sandbox.temp_dir()
    try:
        agent = tmp / "agent"
        archive(ws, tree, agent)
        # A: the agent's tree as it stands, its own tests included
        res_a = run_tests(agent_image(task), "agent", agent, task.tests, task.pytest_args)
        # B: the snapshot's own tests on the agent's code (the agent can't hide a regression by editing tests)
        b = tmp / "b"
        archive(ws, tree, b)
        replace_paths(b, ws, base, [task.tests])
        res_b = run_tests(agent_image(task), "agent", b, task.tests, task.pytest_args)
        # C: the agent's tests on the snapshot's code
        c = tmp / "c"
        archive(ws, base, c)
        replace_paths(c, ws, tree, [task.tests])
        res_c = run_tests(agent_image(task), "agent", c, task.tests, task.pytest_args)
        # F: upstream HEAD's tests on the agent's package code
        f = tmp / "f"
        archive(repo, task.head, f)
        replace_paths(f, ws, tree, task.packages)
        res_f = run_tests(score_image(task), "score", f, task.tests, task.pytest_args)
        patch = git(ws, "diff", "--binary", base, tree)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    new_tests = {t for t in res_a if t not in snap_res}
    verified = sorted({test_function(t) for t in new_tests if res_a[t] == "pass" and res_c.get(t) == "fail"})
    regressed_snapshot = sorted(t for t, s in snap_res.items() if s == "pass" and res_b.get(t) == "fail")
    regressed_future = sorted(t for t, s in control.items() if s == "pass" and res_f.get(t) == "fail")
    regressions = sorted({test_function(t) for t in regressed_snapshot + regressed_future})
    passing = {t for t, s in res_f.items() if s == "pass"}
    target = [t for u in task.units for t in u["tests"]]
    by_label = {}
    for label in ("bug", "feature", "unclear"):
        us = [u for u in task.units if u["label"] == label]
        by_label[label] = {"units": len(us), "all": bug_credit(us, passing, "all"),
                           "any": bug_credit(us, passing, "any"), "fraction": round(bug_credit(us, passing, "fraction"), 4)}
    coll = collisions(repo, task, patch, log=log) if replay and patch.strip() else {"collisions": 0, "skipped": not replay}
    parts = Parts(
        bugs_available=len(task.bug_units),
        bugs_fixed=bug_credit(task.bug_units, passing, weights.bug_credit),
        verified_tests=len(verified),
        regressions=len(regressions),
        collisions=coll["collisions"],
        changed_lines=changed,
        detail={
            "task": task.id, "units": by_label,
            "bugs_fixed_ids": sorted(u["id"] for u in task.bug_units if all(t in passing for t in u["tests"])),
            "target_tests_fixed": sorted(t for t in target if t in passing),
            "target_tests": len(target),
            "verified_tests": verified,
            "regressed_snapshot_tests": regressed_snapshot[:200],
            "regressed_future_tests": regressed_future[:200],
            "changed_files": files,
            "replay": {k: v for k, v in coll.items() if k != "detail"},
        },
    )
    return Reward.from_parts(parts, weights)
