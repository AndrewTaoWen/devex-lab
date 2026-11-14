"""repomap: remember what the test suite runs, then ask what a change will touch without running anything.

  repomap record   [--package src/pkg] [--tests tests] [--python PY] [--json]
        Trace the whole suite once and save the memory to .repomap/memory.json (lines per test,
        call graph, value summaries, and the commit + file hashes it was recorded at), plus each test's
        calls in order with short argument/return values in .repomap/calls.json (--steps N caps rows per
        test, default 500, 0 = off; --no-values; --repr-len N). Values come from your tests and can hold
        fixture secrets: .repomap/ is local data, don't commit it.
  repomap impact   [TARGET ...] [--diff [REV]] [--graph [--level L] [--cutoff C]] [--json]
        TARGET is a function name (or part of one) or path.py:LINE. With --diff (or no target),
        uses your uncommitted changes against the recorded commit. Reads only the memory file.
        Prints the changed functions, their callers grouped by module, the tests that run them,
        a pytest command for those tests, and anything the memory can't vouch for.
  repomap refresh  [--diff [REV]] [--json]
        Re-trace only the tests that run code in the changed files (plus changed test files) and
        merge them into the memory, so the next lookup is current without a full re-record.
  repomap graph    update [--no-full] [--max-share 0.2] [--max-commits 50] [--json]
        Bring the memory and the code graph (.repomap/graph.db) up to date: a full re-trace when
        dependencies or test setup changed or the map is too old, else re-trace only the tests that run
        changed files; then re-read changed files' source.
  repomap graph    show [KEY] [--level line|function|class|module|file] [--json]
        What the graph holds and how stale it is, or one dot's arrows in and out.
  repomap status   [--json]
        What the memory covers and which package files have changed since it was recorded.
  repomap serve    [--port 8765] [--open] [--export FILE.html]
        Open the map in a browser at a localhost link: modules and functions coloured by how many tests
        run them, a function's callers, callees and recorded values, and the impact of your uncommitted
        changes or of any functions you pick. --export writes the same page as one self-contained file.
  repomap query       fn|callers|tests|gaps [ARG] [--public] [--limit N] [--basic] [--json]
        Ask the memory what the suite actually did: value summaries for a function, who calls it,
        which tests run FILE:LINE, or arguments never seen empty / zero / negative.

Run from the repo root. Needs Python 3.12+ in the interpreter that runs the tests.
"""
import argparse, ast, hashlib, json, os, re, subprocess, sys, tempfile, time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
PLUGIN = HERE / "_plugin"  # only the pytest plugin goes on the traced interpreter's path
MEM = Path(".repomap/memory.json")
CALLS = Path(".repomap/calls.json")  # per-test call order and values; kept apart so memory.json stays small


# ---------- helpers ----------

def git(*a, check=True):
    return subprocess.run(["git", *a], capture_output=True, text=True, check=check).stdout


def file_hash(p):
    try:
        return hashlib.sha1(Path(p).read_bytes(), usedforsecurity=False).hexdigest()[:12]
    except OSError:
        return None


def functions(src):
    """[(start, end, qualname)] for every function in a source file, sorted so the innermost wins last."""
    out = []
    def walk(node, prefix):
        for n in ast.iter_child_nodes(node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{prefix}{n.name}"
                out.append((min([n.lineno] + [d.lineno for d in n.decorator_list]), n.end_lineno, q))
                walk(n, q + ".<locals>.")
            elif isinstance(n, ast.ClassDef):
                walk(n, f"{prefix}{n.name}.")
    try:
        walk(ast.parse(src), "")
    except SyntaxError:
        pass
    return sorted(out, key=lambda x: (x[0], -x[1]))


def func_at(fs, line):
    best = None
    for s, e, q in fs:
        if s <= line <= e:
            best = q
    return best


def module(key):
    return re.sub(r"(/__init__)?\.py$", "", key.split(":", 1)[0]).replace("/", ".")


def load():
    if not MEM.exists():
        sys.exit("No memory yet. Run: repomap record")
    return json.loads(MEM.read_text())


def save(m):
    MEM.parent.mkdir(exist_ok=True)
    MEM.write_text(json.dumps(m, separators=(",", ":")))


def trace(m, test_args, out):
    opts = m.get("trace_opts", {})
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(Path(m["package"]).parent), str(PLUGIN),
                                                        os.environ.get("PYTHONPATH", "")]),
           "REPOMAP_ROOT": m["package"], "REPOMAP_OUT": str(out), "REPOMAP_EDGES": "1",
           "REPOMAP_STEPS": str(opts.get("steps", 500)), "REPOMAP_VALUES": "1" if opts.get("values", True) else "0",
           "REPOMAP_REPR": str(opts.get("repr_len", 80))}
    cmd = [m["python"], "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", "-o", "addopts=",
           "-p", "repomap_trace", *test_args]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if not Path(out).exists():
        sys.exit(f"Tracing failed:\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    return json.loads(Path(out).read_text())


def worktree_snapshot(keep=True):
    """A git tree of the working tree as it is now (tracked + untracked, minus ignored), kept alive by
    refs/repomap/memory. The memory remembers this tree, so later diffs are against exactly what was traced."""
    with tempfile.TemporaryDirectory() as td:
        env = {**os.environ, "GIT_INDEX_FILE": f"{td}/index"}
        run = lambda *a: subprocess.run(["git", *a], env=env, capture_output=True, text=True)  # noqa: E731
        if run("rev-parse", "--git-dir").returncode:
            return None
        run("read-tree", "HEAD")
        run("add", "-A", "--", ".", ":(exclude).repomap", ":(exclude,glob)**/__pycache__/**")  # not the memory itself
        tree = run("write-tree").stdout.strip()
    head = git("rev-parse", "HEAD", check=False).strip()
    commit = subprocess.run(["git", "commit-tree", tree, *(["-p", head] if head else []), "-m", "repomap memory"],
                            capture_output=True, text=True, env={**os.environ, "GIT_AUTHOR_NAME": "repomap",
                            "GIT_AUTHOR_EMAIL": "repomap@localhost", "GIT_COMMITTER_NAME": "repomap",
                            "GIT_COMMITTER_EMAIL": "repomap@localhost"}).stdout.strip()
    if commit and keep:
        git("update-ref", "refs/repomap/memory", commit, check=False)
    return commit or None


def package_files(m):
    return sorted(str(p) for p in Path(m["package"]).rglob("*.py"))


# ---------- record ----------

def guess_package():
    cands = [p for p in Path("src").glob("*/__init__.py")] or [p for p in Path(".").glob("*/__init__.py")
                                                             if p.parent.name not in ("tests", "docs")]
    if len(cands) != 1:
        sys.exit("Pass --package (e.g. src/mypkg).")
    return str(cands[0].parent)


def guess_python():
    """The repo's own virtualenv if it has one (the tests need its packages), else this interpreter."""
    for d in (".venv", "venv", ".env", "env"):
        for exe in (Path(d) / "bin" / "python", Path(d) / "Scripts" / "python.exe"):
            if exe.exists():
                return str(exe.absolute())
    return sys.executable


def load_calls():
    try:
        return json.loads(CALLS.read_text())
    except (OSError, ValueError):
        return {}


def save_calls(calls):
    CALLS.parent.mkdir(exist_ok=True)
    CALLS.write_text(json.dumps(calls, separators=(",", ":")))


def record(a):
    m = dict(package=a.package.rstrip("/"), tests=a.tests, python=a.python, commit=worktree_snapshot(),
             trace_opts=dict(steps=getattr(a, "steps", 500), values=not getattr(a, "no_values", False),
                             repr_len=getattr(a, "repr_len", 80)))
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        raw = trace(m, [a.tests], Path(td) / "map.json")
    m.update(prefix=str(Path(m["package"]).parent) + "/", tests_map=raw["tests"], functions=raw["functions"],
             edges=raw["edges"], hashes={f: file_hash(f) for f in package_files(m)}, recorded=time.time())
    m.update(full_commit=m["commit"], full_recorded=m["recorded"])  # the graph's recompute rules count from here
    save(m)
    save_calls(raw.get("calls", {}))
    res = dict(tests=len(raw["tests"]), functions=len(raw["functions"]), edges=len(raw["edges"]),
               seconds=round(time.time() - t0, 1), memory=str(MEM), commit=m["commit"])
    if getattr(a, "json", False):
        print(json.dumps(res, indent=1))
    elif not getattr(a, "quiet", False):
        print(f"Recorded {res['tests']} tests, {res['functions']} functions, {res['edges']} call edges "
              f"in {res['seconds']:.0f}s -> {MEM}")
    return res


# ---------- working out what changed ----------

def old_source(m, path):
    """The file as it was when the memory was last recorded or refreshed, or the current file if unknown."""
    if m.get("commit"):
        src = subprocess.run(["git", "show", f"{m['commit']}:{path}"], capture_output=True, text=True)
        if src.returncode == 0:
            return src.stdout
    return Path(path).read_text() if Path(path).exists() else ""


def changed_targets(m, rev):
    """{key "rel/file.py:Qual"} touched by the diff between the memory's snapshot (or REV) and the working tree,
    plus changed test files. Uses the recorded version of each file to find the functions."""
    base = rev or m.get("commit") or "HEAD"
    now = worktree_snapshot(keep=False) or "HEAD"  # includes untracked files, like the memory's snapshot
    diff = git("diff", "-U0", "--no-renames", base, now, "--", m["package"], m["tests"])
    keys, test_files, cur = set(), set(), None
    fs_cache = {}
    for l in diff.splitlines():
        if l.startswith("--- "):
            cur = None if l == "--- /dev/null" else l[6:]
        elif l.startswith("+++ ") and cur is None:
            cur = l[6:]
        elif l.startswith("@@") and cur:
            if cur.startswith(m["tests"]):
                test_files.add(cur)
                continue
            if not cur.endswith(".py") or not cur.startswith(m["package"]):
                continue
            mm = re.match(r"@@ -(\d+)(?:,(\d+))?", l)
            start, n = int(mm[1]), int(mm[2] or 1)
            fs = fs_cache.setdefault(cur, functions(old_source(m, cur)))
            rel = cur[len(m["prefix"]):]
            hit = False
            for ln in (range(start, start + n) if n else range(start, start + 2)):
                q = func_at(fs, ln)
                if q:
                    keys.add(f"{rel}:{q}")
                    hit = True
            if not hit:
                keys.add(f"{rel}:<module level>")
    return keys, test_files


def resolve(m, targets, unmatched=None):
    """Function names, partial names or path.py:LINE -> memory keys. Names that match nothing go to unmatched."""
    keys = set()
    for t in targets:
        mm = re.match(r"(.+\.py):(\d+)$", t)
        if mm:
            path = mm[1] if mm[1].startswith(m["prefix"]) else m["prefix"] + mm[1]
            q = func_at(functions(old_source(m, path)), int(mm[2]))
            keys.add(f"{path[len(m['prefix']):]}:{q or '<module level>'}")
            continue
        found = [k for k in m["functions"] if k.endswith(":" + t) or k.endswith("." + t)] or \
                [k for k in m["functions"] if t in k]
        if not found:
            if unmatched is None:
                print(f"  (nothing in memory matches {t!r})")
            else:
                unmatched.append(t)
        keys.update(found)
    return keys


# ---------- impact ----------

def lines_index(m):
    """key -> tests that run it, built from per-test lines and the recorded function ranges."""
    by_file = defaultdict(list)  # rel file -> [(test, line)]
    for t, v in m["tests_map"].items():
        for l in v["lines"]:
            f, n = l.rsplit(":", 1)
            by_file[f].append((t, int(n)))
    return by_file


def tests_for(m, keys, by_file):
    out = defaultdict(set)
    cache = {}
    for k in keys:
        rel, q = k.split(":", 1)
        path = m["prefix"] + rel
        fs = cache.setdefault(rel, functions(old_source(m, path)))
        if q in ("<module level>", "<new file>"):
            out[k] = {t for t, _ in by_file.get(rel, [])}  # anything that runs code in the file
            continue
        span = [(s, e) for s, e, qq in fs if qq == q]
        out[k] = {t for t, n in by_file.get(rel, []) if any(s <= n <= e for s, e in span)}
    return out


def callers_of(m, keys, depth=2):
    up = defaultdict(set)
    for caller, callee in m["edges"]:
        if caller != "<test>" and caller != callee:
            up[callee].add(caller)
    seen, frontier, layers = set(keys), set(keys), []
    for _ in range(depth):
        nxt = {c for k in frontier for c in up.get(k, ())} - seen
        if not nxt:
            break
        layers.append(nxt)
        seen |= nxt
        frontier = nxt
    return layers


def compute_impact(m, targets=(), diff=None, use_diff=True, depth=2):
    """The impact of named targets, or of the working-tree diff against the memory's snapshot (or REV)."""
    t0 = time.time()
    unmatched = []
    if targets:
        keys, test_files = resolve(m, targets, unmatched), set()
        source = "named targets"
    else:
        keys, test_files = changed_targets(m, diff)
        source = f"changes since {(diff or m.get('commit') or 'HEAD')[:10]}"
    by_file = lines_index(m)
    per_key = tests_for(m, keys, by_file)
    tests = set().union(*per_key.values()) if per_key else set()
    for tf in test_files:
        tests |= {t for t in m["tests_map"] if t.startswith(tf)} or {tf}
    layers = callers_of(m, {k for k in keys if not k.endswith(">")}, depth)
    unknown = sorted(k for k in keys if not per_key.get(k) and not k.endswith(">"))
    stale = sorted(f for f, h in m["hashes"].items() if file_hash(f) != h)
    return dict(source=source, changed=sorted(keys), changed_tests={k: len(v) for k, v in per_key.items()},
                test_files=sorted(test_files), callers=[sorted(l) for l in layers], tests=sorted(tests),
                untested=unknown, unmatched=unmatched, files_changed_since_record=stale,
                seconds=round(time.time() - t0, 2))


def compute_graph_impact(m, targets=(), diff=None, level="function", cutoff=None):
    """Impact from the code graph: tests (closest first), downstream code with confidence, public functions
    at risk, and changed or affected code no test runs. Targets may name functions no test runs, too."""
    from . import graph
    if not graph.DB.exists():
        sys.exit("No graph yet. Run: repomap graph update")
    db = graph.connect()
    t0 = time.time()
    unmatched = []
    if targets:
        dots = [k for (k,) in db.execute("SELECT key FROM dots WHERE kind = 'function'")]
        keys = set()
        for t in targets:
            if re.match(r"(.+\.py):(\d+)$", t):
                keys |= resolve(m, [t], unmatched)
                continue
            found = [k for k in dots if k.endswith(":" + t) or k.endswith("." + t)] or [k for k in dots if t in k]
            keys.update(found) if found else unmatched.append(t)
        test_files, source = set(), "named targets"
    else:
        keys, test_files = changed_targets(m, diff)
        source = f"changes since {(diff or m.get('commit') or 'HEAD')[:10]}"
    res = graph.impact(db, keys, level=level, **({"cutoff": cutoff} if cutoff is not None else {}))
    have = {t["test"] for t in res["tests"]}
    for tf in sorted(test_files):  # an edited test file: run its tests
        res["tests"] += [dict(test=t, reason="test file changed", depth=None, confidence=1.0)
                         for t in sorted(m["tests_map"]) if t.startswith(tf) and t not in have]
    meta = dict(db.execute("SELECT key, value FROM meta"))
    stale = graph.changed_files(db, m["package"])
    res.update(source=source, test_files=sorted(test_files), unmatched=unmatched, total_tests=len(m["tests_map"]),
               graph_commit=meta.get("commit"), files_changed_since_graph=stale, seconds=round(time.time() - t0, 2),
               source_only=db.execute("SELECT COUNT(*) FROM dots WHERE kind = 'function' AND traced = 0").fetchone()[0])
    return res


def show_graph_impact(m, res):
    for t in res["unmatched"]:
        print(f"  (nothing in the graph matches {t!r})")
    print(f"Impact of {res['source']}  (graph at {(res['graph_commit'] or '?')[:10]}: {res['total_tests']} tests "
          f"traced, {res['source_only']} function(s) seen only in source; level {res['level']})\n")
    if not res["changed"] and not res["test_files"]:
        print("No changes to library or test code.")
        return
    print("Changed")
    for k in res["changed"]:
        print(f"  {k}")
    for tf in res["test_files"]:
        print(f"  {tf}  (test file)")
    sure = [t for t in res["tests"] if t["confidence"] == 1.0]
    print(f"\nTests to run  ({len(sure)} of {res['total_tests']} run changed code; closest first)")
    for t in res["tests"][:30]:
        tag = ("direct" if t["depth"] == 1 else f"{t['depth']} deep") if t["depth"] else (
            f"{t['confidence']:.2f}" if t["confidence"] < 1 else "runs it")
        print(f"  {tag:>8}  {t['test']}" + ("  (depends on it)" if t["confidence"] < 1 else ""))
    if len(res["tests"]) > 30:
        print(f"            … {len(res['tests']) - 30} more")
    if res["downstream"]:
        print("\nCode that may now behave differently")
        for d in res["downstream"][:30]:
            note = "  seen only in source" if d["source"] == "static" else ""
            print(f"  {d['confidence']:.2f}  {d['key']}   via {d['via']}{note}")
    if res["public"]:
        print("\nPublic functions at risk  (what users of the package call)")
        print("  " + ", ".join(res["public"]))
    if res["untested"]:
        print("\nNo test covers")
        for k in res["untested"]:
            print(f"  {k}")
    if res["files_changed_since_graph"]:
        print(f"\n{len(res['files_changed_since_graph'])} package files changed since the graph was built; "
              "run `repomap graph update`.")
    sel = [t["test"] for t in sure] or [t["test"] for t in res["tests"]]
    if sel:
        print(f"\nRun them:\n  {m['python']} -m pytest -q " + " ".join(sel[:40]) + (" …" if len(sel) > 40 else ""))


def impact(a):
    m = load()
    if a.graph:
        res = compute_graph_impact(m, a.targets if not a.diff_given else (), a.diff, a.level, a.cutoff)
        print(json.dumps(res, indent=1)) if a.json else show_graph_impact(m, res)
        return
    res = compute_impact(m, a.targets if not a.diff_given else (), a.diff, depth=a.depth)
    keys, test_files, tests, unknown, stale = (set(res["changed"]), res["test_files"], set(res["tests"]),
                                               res["untested"], res["files_changed_since_record"])
    layers, source = [set(l) for l in res["callers"]], res["source"]
    per_key = {k: range(n) for k, n in res["changed_tests"].items()}
    if a.json:
        print(json.dumps(res, indent=1))
        return
    for t in res["unmatched"]:
        print(f"  (nothing in memory matches {t!r})")
    print(f"Impact of {source}  ({len(m['tests_map'])} tests in memory, looked up in {res['seconds']}s)\n")
    if not keys and not test_files:
        print("No changes to library or test code.")
        return
    print("Changed:")
    for k in sorted(keys):
        print(f"  {k}  ({len(per_key.get(k, ()))} tests)")
    for tf in sorted(test_files):
        print(f"  {tf}  (test file)")
    if layers:
        print("\nCallers (pieces that depend on it):")
        for i, l in enumerate(layers, 1):
            by_mod = defaultdict(list)
            for k in l:
                by_mod[module(k)].append(k.split(":", 1)[1])
            for mod in sorted(by_mod):
                names = sorted(by_mod[mod])
                more = f" … +{len(names) - 6}" if len(names) > 6 else ""
                print(f"  {'  ' * (i - 1)}{'direct' if i == 1 else f'level {i}'}  {mod}: {', '.join(names[:6])}{more}")
    by_tf = defaultdict(int)
    for t in tests:
        by_tf[t.split("::", 1)[0]] += 1
    print(f"\nTests that run it: {len(tests)} of {len(m['tests_map'])}")
    for tf, n in sorted(by_tf.items(), key=lambda x: -x[1])[:15]:
        print(f"  {n:4d}  {tf}")
    if len(by_tf) > 15:
        print(f"        … {len(by_tf) - 15} more files")
    if unknown:
        print("\nNo test in memory runs these, so the memory can't say what depends on them:")
        for k in unknown:
            print(f"  {k}")
    if stale:
        print(f"\n{len(stale)} library files changed since recording; run `repomap refresh` to update.")
    if tests:
        sel = sorted({t.split('[')[0] for t in tests}) if len(tests) < 40 else sorted(by_tf)
        print(f"\nRun them:\n  {m['python']} -m pytest -q " + " ".join(sel[:40]) + (" …" if len(sel) > 40 else ""))


# ---------- refresh ----------

def refresh(a):
    t0 = time.time()
    m = load()
    keys, test_files = changed_targets(m, a.diff)
    files = {k.split(":", 1)[0] for k in keys}
    by_file = lines_index(m)
    rerun = {t for f in files for t, _ in by_file.get(f, [])}
    targets = sorted({t.split("::", 1)[0] for t in rerun} | test_files) if a.by_file else sorted(rerun | test_files)
    if not targets:
        res = dict(retraced=0, tests=len(m["tests_map"]), changed_files=[], seconds=round(time.time() - t0, 1))
        if getattr(a, "json", False):
            print(json.dumps(res, indent=1))
        elif not getattr(a, "quiet", False):
            print("Nothing to refresh.")
        return res
    with tempfile.TemporaryDirectory() as td:
        args = targets
        if len(" ".join(args)) > 100_000:  # very long node-id lists: fall back to whole test files
            args = sorted({t.split("::", 1)[0] for t in targets})
        raw = trace(m, args, Path(td) / "map.json")
    # line numbers in changed files are only valid for re-traced tests; drop everyone else's lines there
    # (by construction nobody else ran those files), then merge
    for t in list(m["tests_map"]):
        if t in raw["tests"] or any(t.startswith(tf) for tf in test_files):
            m["tests_map"].pop(t, None)
    m["tests_map"].update(raw["tests"])
    calls = load_calls()
    calls = {t: v for t, v in calls.items() if t in m["tests_map"] and t not in raw["tests"]}
    calls.update(raw.get("calls", {}))
    save_calls(calls)
    touched = {m["prefix"] + f for f in files}
    m["edges"] = [e for e in m["edges"] if not any(x.split(":", 1)[0] in files for x in e)]
    m["edges"] = sorted({tuple(e) for e in m["edges"]} | {tuple(e) for e in raw["edges"]})
    for k, v in raw["functions"].items():
        m["functions"][k] = v
    m["hashes"].update({f: file_hash(f) for f in package_files(m)})
    m["commit"] = worktree_snapshot() or m.get("commit")
    save(m)
    res = dict(retraced=len(raw["tests"]), tests=len(m["tests_map"]), changed_files=sorted(touched),
               seconds=round(time.time() - t0, 1))
    if getattr(a, "json", False):
        print(json.dumps(res, indent=1))
    elif not getattr(a, "quiet", False):
        print(f"Re-traced {res['retraced']} tests (of {res['tests']}) touching {len(touched)} changed files "
              f"in {res['seconds']:.0f}s; memory updated.")
    return res


def status(a):
    m = load()
    stale = sorted(f for f, h in m["hashes"].items() if file_hash(f) != h)
    p = recompute_plan(m)
    res = dict(tests=len(m["tests_map"]), functions=len(m["functions"]), edges=len(m["edges"]),
               package=m["package"], commit=m.get("commit"), recorded=m.get("recorded"), changed_files=stale,
               full_retrace_due=p["full"], full_retrace_reasons=p["reasons"])
    if getattr(a, "json", False):
        print(json.dumps(res, indent=1))
    elif not getattr(a, "quiet", False):
        print(f"Memory: {res['tests']} tests, {res['functions']} functions, recorded at "
              f"snapshot {(res['commit'] or '?')[:10]} (refs/repomap/memory)")
        print(f"Library files changed since: {len(stale)}" + ("".join(f"\n  {f}" for f in stale)))
        if p["full"]:
            print("Full re-trace due: " + "; ".join(p["reasons"]) + " (run `repomap graph update`)")
    return res


# ---------- graph ----------

GRAPH_LEVELS = ("line", "function", "class", "module", "file")


def since_full(m):
    """(files changed since the last full trace, commits since it) - what the recompute rules look at."""
    base = m.get("full_commit") or m.get("commit")
    if not base:
        return [], None
    now = worktree_snapshot(keep=False) or "HEAD"
    files = [f for f in git("diff", "--name-only", "--no-renames", base, now, check=False).splitlines() if f]
    n = git("rev-list", "--count", f"{base}..HEAD", check=False).strip()
    return files, int(n) if n.isdigit() else None


def recompute_plan(m, a=None):
    from . import recompute
    files, commits = since_full(m)
    return recompute.plan(files, m["package"], len(package_files(m)), commits,
                          max_share=getattr(a, "max_share", recompute.MAX_SHARE),
                          max_commits=getattr(a, "max_commits", recompute.MAX_COMMITS))


def graph_update(a):
    """Bring memory and graph up to date: a full re-trace if a recompute rule fires, else a refresh of the
    changed files' tests; then re-read changed files' source into graph.db (or build it if missing)."""
    from . import graph
    m = load()
    p = recompute_plan(m, a)
    stale = sorted(f for f, h in m["hashes"].items() if file_hash(f) != h) or \
        [f for f in package_files(m) if f not in m["hashes"]]
    did = "nothing to re-trace"
    if p["full"] and not a.no_full:
        opts = m.get("trace_opts", {})
        record(argparse.Namespace(package=m["package"], tests=m["tests"], python=m["python"], quiet=True,
                                  steps=opts.get("steps", 500), no_values=not opts.get("values", True),
                                  repr_len=opts.get("repr_len", 80)))
        did = "full re-trace (" + "; ".join(p["reasons"]) + ")"
    elif stale:
        r = refresh(argparse.Namespace(diff=None, by_file=False, quiet=True))
        did = f"re-traced {r['retraced']} tests touching {len(stale)} changed files"
    m = load()
    calls = load_calls()
    if graph.DB.exists():
        db = graph.connect()
        changed = graph.changed_files(db, m["package"])
        graph.update_files(db, m["package"], changed, m, calls)
        read = f"re-read {len(changed)} changed files"
    else:
        db = graph.build(m["package"], m, calls)
        changed, read = None, "built from scratch"
    with db:
        graph.set_meta(db, commit=m.get("commit") or "", built=str(time.time()))
    res = dict(retrace=did, source=read, full_due=p["full"] and a.no_full, reasons=p["reasons"],
               graph=graph.summary(db), db=str(graph.DB))
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"Tests: {did}. Source: {read}.")
        if res["full_due"]:
            print("A full re-trace is due (" + "; ".join(p["reasons"]) + "); run `repomap graph update` without "
                  "--no-full or `repomap record`.")
        s = res["graph"]
        print(f"Graph: {s['dots']} dots ({s['traced']} run by tests), {s['effective_arrows']} arrows in use "
              f"({s['runtime_arrows']} seen in tests, {s['static_arrows']} read from source) -> {graph.DB}")
    return res


def graph_show(a):
    """Without KEY: what the graph holds and how stale it is. With KEY: that dot's arrows in and out."""
    from . import graph
    if not graph.DB.exists():
        sys.exit("No graph yet. Run: repomap graph update")
    db = graph.connect()
    m = load()
    if not a.key:
        p = recompute_plan(m, a)
        meta = dict(db.execute("SELECT key, value FROM meta"))
        source_only = [k for (k,) in db.execute("SELECT key FROM dots WHERE kind = 'function' AND traced = 0 "
                                                 "ORDER BY key")]
        res = dict(graph.summary(db), commit=meta.get("commit"), built=float(meta.get("built") or 0),
                   changed_since_built=graph.changed_files(db, m["package"]), full_due=p["full"],
                   reasons=p["reasons"], source_only=source_only)
        if a.json:
            print(json.dumps(res, indent=1))
            return res
        age = time.time() - res["built"]
        print(f"Graph at snapshot {(res['commit'] or '?')[:10]}, built {age / 3600:.1f}h ago: {res['dots']} dots, "
              f"{res['effective_arrows']} arrows in use, {res['call_rows']} call-order rows for {res['tests']} tests")
        print(f"Files changed since built: {len(res['changed_since_built'])}"
              + "".join(f"\n  {f}" for f in res["changed_since_built"]))
        print(f"Functions no test runs (static arrows only): {len(source_only)}"
              + "".join(f"\n  {k}" for k in source_only[:20]) + ("\n  ..." if len(source_only) > 20 else ""))
        print("Full re-trace due: " + ("; ".join(p["reasons"]) if p["full"] else "no"))
        return res
    arrows = graph.arrows(db, level=a.level)
    nodes = {x for s, d, *_ in arrows for x in (s, d)}
    matches = sorted(n for n in nodes if n == a.key) or sorted(n for n in nodes if a.key in n)
    if not matches:
        sys.exit(f"No dot matches {a.key!r} at level {a.level}.")
    res = {k: dict(callers=sorted({(s, kind, src) for s, d, kind, src in arrows if d == k}),
                   callees=sorted({(d, kind, src) for s, d, kind, src in arrows if s == k})) for k in matches[:10]}
    if a.json:
        print(json.dumps(res, indent=1))
        return res
    for k, v in res.items():
        print(k)
        for label, rows in (("used by", v["callers"]), ("uses", v["callees"])):
            for other, kind, src in rows:
                print(f"  {label:8} {other}  ({kind}, {src})")
    return res


# ---------- serve ----------

def cell(fidx, k):
    """A traced or graph key -> its cell on the page: module code is the module cell, and nested code objects
    (genexprs, lambdas, inner functions) roll into the function that holds them, so calls made through a
    generator expression still connect caller and callee."""
    k = k.replace(":<module>", ":<module level>")
    while k not in fidx and ".<locals>." in k:
        k = k.rsplit(".<locals>.", 1)[0]
    return fidx.get(k)


def ui_data(m):
    """Everything the map page needs, indexed so the browser can work out impact by itself
    (same rules as `impact`: a function's tests are those that ran any line of it)."""
    tests = sorted(m["tests_map"])
    tix = {t: i for i, t in enumerate(tests)}
    by_file = lines_index(m)
    fns, fidx = [], {}
    for path in sorted(m["hashes"]):
        rel = path[len(m["prefix"]):]
        src = old_source(m, path)
        at_line = defaultdict(list)
        fidx[f"{rel}:<module level>"] = len(fns)
        fns.append([f"{rel}:<module level>", 0, 0, set()])
        for s_, e, q in functions(src):
            fidx[f"{rel}:{q}"] = len(fns)
            for n in range(s_, e + 1):
                at_line[n].append(len(fns))
            fns.append([f"{rel}:{q}", s_, e, set()])
        for t, n in by_file.get(rel, []):
            fns[fidx[f"{rel}:<module level>"]][3].add(tix[t])
            for i in at_line.get(n, ()):
                fns[i][3].add(tix[t])
    vals = {}
    for k, f in m["functions"].items():
        if k in fidx:
            vals[fidx[k]] = {"calls": f["calls"], "args": f["args"], "returns": f["returns"], "raises": f["raises"]}
    edges = sorted({(i, j) for a, b in m["edges"] for i, j in [(cell(fidx, a), cell(fidx, b))]
                    if i is not None and j is not None and i != j})
    stale = sorted(f for f, h in m["hashes"].items() if file_hash(f) != h)
    return dict(name=Path.cwd().name, package=m["package"], tests_dir=m["tests"], python=m["python"],
                commit=(m.get("commit") or "")[:10], recorded=m.get("recorded"),
                tests=[[t, m["tests_map"][t]["outcome"]] for t in tests],
                fns=[[k, s_, e, sorted(t)] for k, s_, e, t in fns], vals=vals, edges=edges, stale=stale,
                graph=ui_graph(fidx))


def ui_graph(fidx):
    """What the code graph adds to the page, if .repomap/graph.db exists: the source-only arrows impact uses
    (for code no test runs), which functions are public, and the confidence weights, so the page can score
    a change the same way `impact --graph` does."""
    from . import graph
    if not graph.DB.exists():
        return None
    db = graph.connect()

    sedges = set()
    for a, b, kind in db.execute("SELECT src, dst, kind FROM effective_arrows WHERE source = 'static' AND kind IN "
                                 "('calls', 'calls_by_name', 'inherits')"):
        i, j = cell(fidx, a), cell(fidx, b)
        if i is not None and j is not None and i != j:
            sedges.add((i, j, kind))
    return dict(sedges=sorted(sedges), public=sorted(i for k, i in fidx.items() if graph.is_public(k)),
                step=graph.STEP, static=graph.STATIC_WEIGHT, guess=graph.GUESS_WEIGHT, cutoff=graph.CUTOFF)


def page_with(data, changes):
    page = (HERE / "ui.html").read_text()
    blob = json.dumps({"data": data, "changes": changes}, separators=(",", ":")).replace("</", "<\\/")
    return page.replace('<script id="embedded" type="application/json"></script>',
                        f'<script id="embedded" type="application/json">{blob}</script>')


def serve(a):
    if a.export:
        m = load()
        res = compute_impact(m)
        Path(a.export).write_text(page_with(ui_data(m), res if res["changed"] or res["test_files"] else None))
        print(f"Wrote {a.export} ({Path(a.export).stat().st_size // 1024} KB)")
        return
    cache = {}

    def current():
        mt = MEM.stat().st_mtime  # a refresh in another terminal shows up on the next reload
        if cache.get("mt") != mt:
            m = load()
            cache.update(mt=mt, m=m, data=json.dumps(ui_data(m), separators=(",", ":")).encode())
        return cache

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path == "/":
                    body, kind = (HERE / "ui.html").read_bytes(), "text/html; charset=utf-8"
                elif path == "/api/data":
                    body, kind = current()["data"], "application/json"
                elif path == "/api/changes":
                    body, kind = json.dumps(compute_impact(current()["m"])).encode(), "application/json"
                else:
                    self.send_error(404)
                    return
            except Exception as e:  # keep serving; show the error in the page
                body, kind = json.dumps({"error": str(e)}).encode(), "application/json"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    load()
    for port in range(a.port, a.port + 20):
        try:
            srv = ThreadingHTTPServer((a.host, port), Handler)
            break
        except OSError:
            continue
    else:
        sys.exit(f"No free port from {a.port} to {a.port + 19}.")
    url = f"http://localhost:{port}"
    print(f"repomap map of {Path.cwd().name}: {url}   (Ctrl-C to stop)", flush=True)
    if a.open:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--package", default=None)
    r.add_argument("--tests", default="tests")
    r.add_argument("--python", default=None, help="interpreter with pytest (default: ./.venv if present, else this one)")
    r.add_argument("--steps", type=int, default=500,
                   help="call-order rows kept per test in .repomap/calls.json (0 = don't record call order)")
    r.add_argument("--no-values", action="store_true", help="don't store argument and return values per call")
    r.add_argument("--repr-len", type=int, default=80, help="characters kept of each stored value (default 80)")
    r.add_argument("--json", action="store_true")
    i = sub.add_parser("impact")
    i.add_argument("targets", nargs="*")
    i.add_argument("--diff", nargs="?", const=None, default=False)
    i.add_argument("--depth", type=int, default=2)
    i.add_argument("--graph", action="store_true", help="use the code graph (.repomap/graph.db): ranked tests, "
                   "downstream code with confidence, public functions at risk, untested code")
    i.add_argument("--level", choices=("line", "function", "class", "module", "file"), default="function",
                   help="with --graph: roll the result up to this level")
    i.add_argument("--cutoff", type=float, default=None, help="with --graph: lowest confidence to report (0.3)")
    i.add_argument("--json", action="store_true")
    f = sub.add_parser("refresh")
    f.add_argument("--diff", nargs="?", const=None, default=None)
    f.add_argument("--by-file", action="store_true", help="re-run whole test files instead of single tests")
    f.add_argument("--json", action="store_true")
    sub.add_parser("status").add_argument("--json", action="store_true")
    q = sub.add_parser("query", help="value summaries, callers, tests for a line, or value gaps")
    q.add_argument("kind", choices=["fn", "callers", "tests", "gaps"])
    q.add_argument("arg", nargs="?", default="")
    q.add_argument("--public", action="store_true", help="gaps: drop private and nested functions")
    q.add_argument("--limit", type=int, default=None, help="gaps: keep the N most-called functions")
    q.add_argument("--basic", action="store_true", help="gaps: only the empty/zero/negative probes")
    q.add_argument("--json", action="store_true")
    g = sub.add_parser("graph", help="build, update or inspect the code graph in .repomap/graph.db")
    g.add_argument("action", choices=["update", "show"])
    g.add_argument("key", nargs="?", default="", help="show: a dot (function, class or file) or part of its name")
    g.add_argument("--level", choices=GRAPH_LEVELS, default="function")
    g.add_argument("--no-full", action="store_true", help="update: never run a full re-trace, only report it")
    g.add_argument("--max-share", type=float, default=0.2, help="full re-trace past this share of changed files")
    g.add_argument("--max-commits", type=int, default=50, help="full re-trace past this many commits")
    g.add_argument("--json", action="store_true")
    v = sub.add_parser("serve")
    v.add_argument("--port", type=int, default=8765)
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--open", action="store_true", help="open the link in your browser")
    v.add_argument("--export", metavar="FILE", help="write a self-contained HTML file instead of serving")
    a = ap.parse_args()
    if a.cmd == "record":
        a.package = a.package or guess_package()
        a.python = a.python or guess_python()
        record(a)
    elif a.cmd == "impact":
        a.diff_given = a.diff is not False
        a.diff = a.diff or None
        impact(a)
    elif a.cmd == "refresh":
        refresh(a)
    elif a.cmd == "serve":
        serve(a)
    elif a.cmd == "graph":
        graph_update(a) if a.action == "update" else graph_show(a)
    elif a.cmd == "query":
        from . import query
        query.show(query.run(load(), a.kind, a.arg, public=a.public, limit=a.limit, basic=a.basic), a.kind, a.json)
    else:
        status(a)


if __name__ == "__main__":
    main()
