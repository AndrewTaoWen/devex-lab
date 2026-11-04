"""Replay upstream's post-snapshot history against an agent patch (ported from harness/replay.py).

The patch is committed on top of the snapshot in the task's clone (objects plus a throwaway ref, no
checkout). For each first-parent upstream commit C after the snapshot, a 3-way merge of the agent
commit with C (base = snapshot) is computed with `git merge-tree`; C is a collision when it adds
conflict hunks over the previous commit. Only textual conflicts count: experiment 05 showed that
auto-resolved merges leave half-merged code whose test failures say nothing.
"""
import os
import re
import subprocess
import tempfile


def _g(repo, *args, env=None, input=None):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env={**os.environ, **(env or {})}, input=input)
    if p.returncode not in (0, 1):
        raise RuntimeError(f"git {' '.join(args[:3])}: {p.stderr[-1000:]}")
    return p


def agent_commit(repo, snapshot, patch: str):
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": f"{td}/index", "GIT_AUTHOR_NAME": "agent", "GIT_AUTHOR_EMAIL": "a@a",
               "GIT_COMMITTER_NAME": "agent", "GIT_COMMITTER_EMAIL": "a@a"}
        _g(repo, "read-tree", snapshot, env=env)
        p = _g(repo, "apply", "--cached", "--binary", "--whitespace=nowarn", "-", env=env, input=patch)
        if p.returncode:
            raise RuntimeError(f"agent patch does not apply to the snapshot: {p.stderr[:500]}")
        tree = _g(repo, "write-tree", env=env).stdout.strip()
        return _g(repo, "commit-tree", tree, "-p", snapshot, "-m", "agent", env=env).stdout.strip()


def conflict_hunks(repo, snapshot, ours, theirs):
    p = _g(repo, "merge-tree", "--write-tree", "--name-only", f"--merge-base={snapshot}", ours, theirs)
    lines = p.stdout.splitlines()
    tree = lines[0]
    files = [l for l in lines[1:] if l and not l.startswith(("Auto-merging", "CONFLICT"))]
    out = {}
    for f in dict.fromkeys(files):
        blob = _g(repo, "cat-file", "-p", f"{tree}:{f}").stdout
        out[f] = max(len(re.findall(r"^<<<<<<< ", blob, re.M)), 1)  # modify/delete etc. count as one hunk
    return out


def collisions(repo, task, patch: str, log=print):
    agent = agent_commit(repo, task.snapshot, patch)
    ups = _g(repo, "rev-list", "--first-parent", "--reverse", f"{task.snapshot}..{task.head}").stdout.split()
    prev, hits = {}, []
    for c in ups:
        k = conflict_hunks(repo, task.snapshot, agent, c)
        grew = {f: n - prev.get(f, 0) for f, n in k.items() if n > prev.get(f, 0)}
        if grew:
            hits.append({"commit": c[:10], "new_hunks": sum(grew.values()), "files": sorted(grew)})
        prev = k
    pk = tuple(task.packages)
    return {"collisions": len(hits), "upstream_commits": len(ups),
            "collisions_in_package": sum(1 for h in hits if any(f.startswith(pk) for f in h["files"])),
            "detail": hits}
