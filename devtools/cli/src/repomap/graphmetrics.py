"""Per-function numbers for the readable Graph tab, read from the code graph (.repomap/graph.db) and git.

Each unit is a function or a module's top-level code. Nested code (genexprs, lambdas, inner defs) folds into the
function that holds it, as on the map page.

  references   distinct places that refer to it directly: functions that call it (seen in tests or read from
               source) and tests that call it themselves
  dependents   distinct functions that reach it through any chain of calls: what may break when it changes
  reach        distinct functions that run underneath it in the recorded tests (the call order in test_calls).
               Pipelines reach far, helpers reach little. Code no test runs falls back to the source arrows.
  tested       the tracer saw it run (module code runs at import, so it counts)
  tests        tests that run any line of it
  calls        how many times the traced suite called it
  changes      commits in the git window that edited its lines
  pace         slow (many dependents, unchanged in the window), fast (changed 2+ times) or ordinary
"""
import json, os, subprocess
from collections import defaultdict
from pathlib import Path

from . import graph

DEFAULT_SINCE = "12 months ago"
CALL_KINDS = ("calls", "calls_by_name")


def unit(key):
    """The unit a key counts towards: nested code objects fold into the function that holds them."""
    return key.split(".<locals>.", 1)[0] if ".<locals>." in key else key


def units(db):
    """key -> (kind, traced) for every function and module's top-level code, nested code left out."""
    return {key: (kind, bool(traced)) for key, kind, traced in db.execute(
        "SELECT key, kind, traced FROM dots WHERE kind IN ('function', 'module_level')") if "<locals>" not in key}


def closure(start, neighbours):
    """Everything reachable from start (start itself left out)."""
    seen, todo = set(), [start]
    while todo:
        for nxt in neighbours.get(todo.pop(), ()):
            if nxt not in seen and nxt != start:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def call_maps(db):
    """(callers, callees) between units over the arrows impact uses: runtime calls, plus source calls for code no
    test ran."""
    callers, callees = defaultdict(set), defaultdict(set)
    for src, dst in db.execute("SELECT src, dst FROM effective_arrows WHERE kind IN (%s)"
                               % ",".join("?" * len(CALL_KINDS)), CALL_KINDS):
        src, dst = unit(src), unit(dst)
        if src != dst and ":" in src:
            callers[dst].add(src)
            callees[src].add(dst)
    return callers, callees


def traced_reach(db):
    """unit -> distinct units that ran underneath it, over every recorded test's call tree."""
    reach = defaultdict(set)
    rows_by_test = defaultdict(list)
    for test, seq, parent, function in db.execute("SELECT test, seq, parent_seq, function FROM test_calls "
                                                  "ORDER BY test, seq"):
        rows_by_test[test].append((seq, parent, unit(function)))
    for rows in rows_by_test.values():
        below = {}
        for seq, parent, function in reversed(rows):  # seq is pre-order, so children come after their parent
            mine = below.pop(seq, set())
            reach[function] |= mine - {function}
            if parent is not None:
                below.setdefault(parent, set()).update(mine | {function})
    return reach


def git_changes(db, since=DEFAULT_SINCE, cwd="."):
    """(unit -> commits that edited it, commits looked at). since=None reads all history."""
    from .cli import functions, func_at
    meta = dict(db.execute("SELECT key, value FROM meta"))
    package = Path(cwd, meta.get("package") or ".").resolve()

    def git(*args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout

    top = git("rev-parse", "--show-toplevel").strip()
    if not top:
        return {}, 0
    base = os.path.relpath(package.parent, top)
    strip = "" if base == "." else base.rstrip("/") + "/"
    window = [f"--since={since}"] if since else []
    commits = git("log", "--format=%H", *window, "--", os.path.relpath(package, top)).split()
    changes = defaultdict(set)
    for sha in commits:
        patch = git("show", "--format=", "--no-renames", "-U0", sha, "--", os.path.relpath(package, top))
        hunks = defaultdict(lambda: ([], []))  # path -> (old lines, new lines)
        old_path = new_path = None
        for line in patch.splitlines():
            if line.startswith("--- "):
                old_path = line[6:] if line.startswith("--- a/") else None
            elif line.startswith("+++ "):
                new_path = line[6:] if line.startswith("+++ b/") else None
            elif line.startswith("@@"):
                old, new = line.split()[1:3]
                for side, spec, path in ((0, old[1:], old_path), (1, new[1:], new_path)):
                    if path and path.endswith(".py"):
                        start, _, count = spec.partition(",")
                        count = int(count) if count else 1
                        hunks[path][side].extend(range(int(start), int(start) + max(count, 1)))
        for path, (old_lines, new_lines) in hunks.items():
            if not path.startswith(strip):
                continue
            rel = path[len(strip):]
            for rev, lines in ((f"{sha}^", old_lines), (sha, new_lines)):
                if not lines:
                    continue
                spans = functions(git("show", f"{rev}:{path}"))
                for number in lines:
                    qualname = func_at(spans, number)
                    changes[unit(f"{rel}:{qualname}" if qualname else f"{rel}:<module>")].add(sha)
    return changes, len(commits)


def pace(dependents, changes, slow_floor):
    if changes >= 2:
        return "fast"
    if changes == 0 and dependents >= slow_floor:
        return "slow"
    return "ordinary"


def metrics(db, memory=None, since=DEFAULT_SINCE, cwd="."):
    """Every unit's numbers. memory (memory.json) adds runtime call counts; since=None reads all git history."""
    kinds = units(db)
    callers, callees = call_maps(db)
    references = defaultdict(set)
    for src, dst in db.execute("SELECT src, dst FROM arrows WHERE kind IN (%s)" % ",".join("?" * len(CALL_KINDS)),
                               CALL_KINDS):
        if ":" in src and unit(src) != unit(dst):
            references[unit(dst)].add(unit(src))
    for test, function in db.execute("SELECT DISTINCT test, function FROM test_calls WHERE parent_seq IS NULL"):
        references[unit(function)].add(test)
    tests = defaultdict(set)
    for test, function in db.execute("SELECT test, function FROM covers"):
        tests[unit(function)].add(test)
    calls = defaultdict(int)
    for key, info in (memory or {}).get("functions", {}).items():
        calls[unit(key)] += info.get("calls", 0)
    reach = traced_reach(db)
    changes, commits = git_changes(db, since, cwd)
    rows = []
    for key in sorted(kinds):
        kind, traced = kinds[key]
        downstream = reach.get(key, set()) if traced else closure(key, callees)
        rows.append(dict(key=key, kind=kind, tested=traced, references=len(references[key]),
                         dependents=len(closure(key, callers)), reach=len(downstream),
                         reach_from="tests" if traced else "source", tests=len(tests[key]), calls=calls[key],
                         changes=len(changes.get(key, ()))))
    with_dependents = sorted(row["dependents"] for row in rows if row["dependents"])
    slow_floor = max(3, with_dependents[3 * len(with_dependents) // 4]) if with_dependents else 3
    for row in rows:
        row["pace"] = pace(row["dependents"], row["changes"], slow_floor)
    return dict(since=since or "all history", commits=commits, slow_floor=slow_floor, functions=rows)


def load_memory(cwd="."):
    path = Path(cwd, ".repomap/memory.json")
    return json.loads(path.read_text()) if path.exists() else None
