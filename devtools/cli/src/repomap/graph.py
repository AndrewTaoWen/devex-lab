"""The code graph: one SQLite file (.repomap/graph.db) that merges what the tests ran with what the source says.

Two layers of arrows share one set of dots:
  runtime  call edges the tracer saw while the suite ran (memory.json "edges"). The source of truth.
  static   calls, imports and subclasses read from the source (repomap.static). A fallback: a static call arrow
           counts only when no test ever ran its caller. imports/inherits have no runtime twin, so they always count.

Tables
  dots(key, kind, parent, file, start, end, traced)       module | module_level | class | function; parent links
                                                          give roll-ups to class, module and file
  arrows(src, dst, kind, source, file)                    file = the file the arrow was read from (src's file)
  tests(test, outcome)
  covers(test, function, lines)                           lines = JSON list of line numbers that test ran there
  test_runs(test, steps, truncated)                       call order per test, capped (see repomap_trace)
  test_calls(test, seq, depth, parent_seq, function, args, ret, raised)
  files(file, hash)                                       what the static layer was built from
  meta(key, value)                                        commit, built time, package
View
  effective_arrows                                        the arrows impact walks: runtime + static fallback

Values in test_calls come from the test suite and can contain fixture secrets. graph.db is local data.
"""
import hashlib, json, re, sqlite3, time
from pathlib import Path

from . import static

DB = Path(".repomap/graph.db")
CALL_KINDS = ("calls", "calls_by_name")

SCHEMA = """
CREATE TABLE IF NOT EXISTS dots(key TEXT PRIMARY KEY, kind TEXT NOT NULL, parent TEXT, file TEXT NOT NULL,
                                start INT, "end" INT, traced INT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS arrows(src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
                                  file TEXT NOT NULL, PRIMARY KEY(src, dst, kind, source));
CREATE INDEX IF NOT EXISTS arrows_dst ON arrows(dst);
CREATE INDEX IF NOT EXISTS arrows_file ON arrows(file);
CREATE TABLE IF NOT EXISTS tests(test TEXT PRIMARY KEY, outcome TEXT);
CREATE TABLE IF NOT EXISTS covers(test TEXT NOT NULL, function TEXT NOT NULL, lines TEXT NOT NULL,
                                  PRIMARY KEY(test, function));
CREATE INDEX IF NOT EXISTS covers_function ON covers(function);
CREATE TABLE IF NOT EXISTS test_runs(test TEXT PRIMARY KEY, steps INT NOT NULL, truncated INT NOT NULL);
CREATE TABLE IF NOT EXISTS test_calls(test TEXT NOT NULL, seq INT NOT NULL, depth INT NOT NULL, parent_seq INT,
                                      function TEXT NOT NULL, args TEXT, ret TEXT, raised TEXT,
                                      PRIMARY KEY(test, seq));
CREATE INDEX IF NOT EXISTS test_calls_function ON test_calls(function);
CREATE TABLE IF NOT EXISTS files(file TEXT PRIMARY KEY, hash TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE VIEW IF NOT EXISTS effective_arrows AS
  SELECT src, dst, kind, source FROM arrows WHERE source = 'runtime'
  UNION
  SELECT a.src, a.dst, a.kind, a.source FROM arrows a
   WHERE a.source = 'static'
     AND (a.kind NOT IN ('calls', 'calls_by_name')
          OR NOT EXISTS (SELECT 1 FROM dots d WHERE d.key = a.src AND d.traced = 1));
"""


def connect(path=DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def file_of(key):
    return key.split(":", 1)[0]


def _hash(path):
    return hashlib.sha1(Path(path).read_bytes(), usedforsecurity=False).hexdigest()[:12]


# ---------- building ----------

def put_static(db, extracted):
    """Write repomap.static's dots and arrows into the static layer."""
    # one key can name several defs (a property's getter and setter, a redefinition): keep one dot whose span
    # covers them all, so lines in any of them map to the key. Narrower dots inside the span still win.
    merged = {}
    for dot in extracted["dots"]:
        kept = merged.get(dot["key"])
        if kept is None:
            merged[dot["key"]] = dict(dot)
        else:
            kept["start"], kept["end"] = min(kept["start"], dot["start"]), max(kept["end"], dot["end"])
    db.executemany('INSERT OR REPLACE INTO dots(key, kind, parent, file, start, "end", traced) '
                   'VALUES(?, ?, ?, ?, ?, ?, COALESCE((SELECT traced FROM dots WHERE key = ?), 0))',
                   [(dot["key"], dot["kind"], dot["parent"], file_of(dot["key"]), dot["start"], dot["end"],
                     dot["key"]) for dot in merged.values()])
    db.executemany("INSERT OR IGNORE INTO arrows VALUES(?, ?, ?, 'static', ?)",
                   [(src, dst, kind, file_of(src)) for src, dst, kind in extracted["arrows"]])


def put_runtime(db, memory, calls):
    """Replace the runtime layer from the tracer's memory (memory.json) and call order (calls.json)."""
    for table in ("tests", "covers", "test_runs", "test_calls"):
        db.execute(f"DELETE FROM {table}")
    db.execute("DELETE FROM arrows WHERE source = 'runtime'")
    db.execute("UPDATE dots SET traced = 0")
    # functions the tracer saw but the source layer doesn't know (a file that no longer parses, say)
    spans = {}
    for key, info in memory.get("functions", {}).items():
        db.execute('INSERT OR IGNORE INTO dots(key, kind, parent, file, start, "end") VALUES(?, ?, ?, ?, ?, ?)',
                   (key, "module_level" if key.endswith(":<module>") else "function", file_of(key), file_of(key),
                    info.get("first"), info.get("first")))
        db.execute("UPDATE dots SET traced = 1 WHERE key = ?", (key,))
    for key, start, end in db.execute("SELECT key, start, \"end\" FROM dots WHERE kind IN ('function', 'class', "
                                      "'module_level')"):
        spans.setdefault(file_of(key), []).append((start or 0, end or 0, key))
    for file_spans in spans.values():
        file_spans.sort(key=lambda span: (span[0], -span[1]))
    edges = memory.get("edges", [])
    db.executemany("INSERT OR IGNORE INTO arrows VALUES(?, ?, 'calls', 'runtime', ?)",
                   [(caller, callee, file_of(caller)) for caller, callee in edges if ":" in caller])
    db.executemany("INSERT OR IGNORE INTO arrows VALUES(?, ?, 'test_calls', 'runtime', 'tests')",
                   [(caller, callee) for caller, callee in edges if ":" not in caller])
    rows = []
    for test, info in memory.get("tests_map", {}).items():
        db.execute("INSERT OR REPLACE INTO tests VALUES(?, ?)", (test, info.get("outcome")))
        lines_by_function = {}
        for ref in info.get("lines", []):
            path, _, line = ref.rpartition(":")
            line = int(line)
            key = innermost(spans.get(path, ()), line) or f"{path}:<module>"
            lines_by_function.setdefault(key, []).append(line)
        rows += [(test, key, json.dumps(sorted(lines))) for key, lines in lines_by_function.items()]
    db.executemany("INSERT OR REPLACE INTO covers VALUES(?, ?, ?)", rows)
    for test, run in calls.items():
        db.execute("INSERT OR REPLACE INTO test_runs VALUES(?, ?, ?)", (test, run["steps"], int(run["truncated"])))
        db.executemany("INSERT OR REPLACE INTO test_calls VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                       [(test, seq, depth, parent, function, json.dumps(args) if args is not None else None,
                         json.dumps(ret) if ret is not None else None, raised)
                        for seq, depth, parent, function, args, ret, raised in run["rows"]])


def innermost(spans, line):
    """The narrowest dot whose lines include line. spans are (start, end, key), sorted by start."""
    best = None
    for start, end, key in spans:
        if start > line:
            break
        if start <= line <= end:
            best = key
    return best


def build(package, memory, calls=None, path=DB, commit=None):
    """Build graph.db from scratch: static layer from the package source, runtime layer from the memory."""
    path = Path(path)
    if path.exists():
        path.unlink()
    db = connect(path)
    with db:
        put_static(db, static.extract(package))
        record_files(db, package)
        put_runtime(db, memory, calls or {})
        set_meta(db, package=str(package), commit=commit or memory.get("commit") or "", built=str(time.time()))
    return db


def update_files(db, package, files, memory=None, calls=None):
    """Re-read the static layer of the given files (relative keys like 'pkg/mod.py'). Other files' symbols are
    re-parsed for name resolution, but only these files' dots and arrows are replaced. With memory, also reload the
    runtime layer (cheap: it is a copy of memory.json)."""
    files = set(files)
    with db:
        for file in files:  # dots the new source no longer has go; traced ones stay until the runtime layer drops them
            db.execute("DELETE FROM arrows WHERE source = 'static' AND file = ?", (file,))
            db.execute("DELETE FROM dots WHERE file = ? AND traced = 0", (file,))
        put_static(db, static.extract(package, files=files))
        record_files(db, package)
        if memory is not None:
            put_runtime(db, memory, calls or {})
    return db


def record_files(db, package):
    base = Path(package).resolve().parent
    db.execute("DELETE FROM files")
    db.executemany("INSERT INTO files VALUES(?, ?)",
                   [(path.relative_to(base).as_posix(), _hash(path))
                    for path in sorted(Path(package).resolve().rglob("*.py")) if "__pycache__" not in path.parts])


def set_meta(db, **values):
    db.executemany("INSERT OR REPLACE INTO meta VALUES(?, ?)", list(values.items()))


def changed_files(db, package):
    """Files whose content differs from what the static layer was built from (added, edited or deleted)."""
    old = dict(db.execute("SELECT file, hash FROM files"))
    base = Path(package).resolve().parent
    now = {path.relative_to(base).as_posix(): _hash(path) for path in Path(package).resolve().rglob("*.py")
           if "__pycache__" not in path.parts}
    return sorted(file for file in old.keys() | now.keys() if old.get(file) != now.get(file))


# ---------- reading ----------

LEVELS = ("line", "function", "class", "module", "file")


def rollup(db, key, level):
    """The dot that stands for key at a zoom level. function: the outermost function around key (a genexpr or
    nested def rolls into the function that holds it); class: the nearest class; module/file: the file."""
    if level == "line":
        return key
    if level in ("module", "file"):
        return file_of(key)
    chain = []  # key, then its parent, its parent's parent ... up to the module
    current = key
    while current:
        row = db.execute("SELECT kind, parent FROM dots WHERE key = ?", (current,)).fetchone()
        if not row:
            break
        chain.append((current, row[0]))
        current = row[1]
    if level == "class":
        return next((dot for dot, kind in chain if kind == "class"), file_of(key))
    functions = [dot for dot, kind in chain if kind in ("function", "module_level")]
    return functions[-1] if functions else key


def arrows(db, effective=True, level="function"):
    """Arrows as (src, dst, kind, source), rolled up to a level (self-arrows from the roll-up dropped)."""
    table = "effective_arrows" if effective else "arrows"
    rows = db.execute(f"SELECT src, dst, kind, source FROM {table}").fetchall()
    if level == "line":
        return sorted(set(rows))
    cache = {}
    def up(key):
        if key not in cache:
            cache[key] = rollup(db, key, level) if ":" in key or level in ("module", "file") else key
        return cache[key]
    out = set()
    for src, dst, kind, source in rows:
        rolled_src, rolled_dst = up(src), up(dst)
        if rolled_src != rolled_dst:
            out.add((rolled_src, rolled_dst, kind, source))
    return sorted(out)


def test_calls(db, test):
    """One test's calls in order: [{seq, depth, parent_seq, function, args, ret, raised}]."""
    rows = db.execute("SELECT seq, depth, parent_seq, function, args, ret, raised FROM test_calls WHERE test = ? "
                      "ORDER BY seq", (test,))
    return [dict(seq=seq, depth=depth, parent_seq=parent_seq, function=function,
                 args=json.loads(args) if args else None, ret=json.loads(ret) if ret else None, raised=raised)
            for seq, depth, parent_seq, function, args, ret, raised in rows]


def summary(db):
    def count(sql):
        return db.execute(sql).fetchone()[0]
    return dict(dots=count("SELECT COUNT(*) FROM dots"), traced=count("SELECT COUNT(*) FROM dots WHERE traced = 1"),
                runtime_arrows=count("SELECT COUNT(*) FROM arrows WHERE source = 'runtime'"),
                static_arrows=count("SELECT COUNT(*) FROM arrows WHERE source = 'static'"),
                effective_arrows=count("SELECT COUNT(*) FROM effective_arrows"),
                tests=count("SELECT COUNT(*) FROM tests"), call_rows=count("SELECT COUNT(*) FROM test_calls"))


# ---------- impact ----------

STEP = 0.9          # confidence kept per arrow followed backwards
STATIC_WEIGHT = 0.5  # an arrow read only from source counts half
GUESS_WEIGHT = 0.8   # and a call linked only by a unique method name counts a little less again
CUTOFF = 0.3         # stop walking once confidence drops below this
WALK_KINDS = ("calls", "calls_by_name", "inherits")


def is_public(key):
    """A function a package user could call: no _private segment in its path or name, not nested, not module code."""
    path, _, q = key.partition(":")
    if not q or q.startswith("<") or "<locals>" in q:
        return False
    parts = [p for p in path[:-3].split("/") if p != "__init__"] + q.split(".")
    return not any(p.startswith("_") and not (p.startswith("__") and p.endswith("__")) for p in parts)


def expand(db, key):
    """A changed function and the code objects nested in it (genexprs, lambdas, inner defs), which the tracer
    reports under their own keys. A module-level change expands to everything in the file."""
    path, _, q = key.partition(":")
    if q in ("<module>", "<module level>", "<new file>"):
        return {k for (k,) in db.execute("SELECT key FROM dots WHERE file = ? AND kind != 'module'", (path,))} \
            | {f"{path}:<module>"}
    nested = {k for (k,) in db.execute("SELECT key FROM dots WHERE key LIKE ? ESCAPE '\\'",
                                        (key.replace("_", "\\_").replace("%", "\\%") + ".<locals>.%",))}
    return {key} | nested


def impact(db, changed, level="function", cutoff=CUTOFF, step=STEP, static_weight=STATIC_WEIGHT,
           guess_weight=GUESS_WEIGHT):
    """What a change to the given keys reaches, walking effective arrows backwards with falling confidence.

    Returns tests (every test that ran changed code, closest first, then tests that only ran downstream code),
    downstream (code that may now behave differently, with confidence, the next hop towards the change and
    whether the weakest arrow on the way was seen in tests or read from source), public (functions a package
    user calls that are changed or downstream), untested (changed or downstream functions no test runs)."""
    changed = {("%s:<module>" % k.split(":")[0]) if k.endswith(":<module level>") else k for k in changed}
    up = {}
    for src, dst, kind, source in db.execute(
            "SELECT src, dst, kind, source FROM effective_arrows WHERE kind IN (%s)" % ",".join("?" * len(WALK_KINDS)),
            WALK_KINDS):
        w = step if source == "runtime" else step * static_weight * (guess_weight if kind == "calls_by_name" else 1)
        up.setdefault(dst, []).append((src, w, source))
    seeds = {}
    for k in changed:
        for x in expand(db, k):
            # a module-level change (a constant, say) reaches the file's functions without an arrow: count it
            # as one static step
            seeds[x] = 1.0 if x == k or not k.endswith(":<module>") else step * static_weight
    score = dict(seeds)
    via, kind_of = {}, {k: "changed" for k in seeds}
    frontier = sorted(seeds, key=lambda k: -seeds[k])
    while frontier:  # best-first: a dot's score is its strongest path to a change
        k = frontier.pop(0)
        for src, w, source in up.get(k, ()):
            s = score[k] * w
            if s >= cutoff and s > score.get(src, 0):
                score[src], via[src] = s, k
                kind_of[src] = "static" if source == "static" or kind_of.get(k) == "static" else "runtime"
                frontier.append(src)
        frontier.sort(key=lambda k: -score[k])
    direct = set(seeds)
    downstream = {k: s for k, s in score.items() if k not in direct and not k.startswith("<")}
    # tests: those that ran changed code are certain (the trace says so); then those that ran downstream code
    ran = {}
    for test, fn in db.execute("SELECT test, function FROM covers"):
        if fn in direct:
            ran.setdefault(test, set()).add(fn)
    depth = {}
    if ran:
        for test, fn, d in db.execute("SELECT test, function, MIN(depth) FROM test_calls GROUP BY test, function"):
            if fn in direct and test in ran:
                depth[test] = min(depth.get(test, d), d)
    tests = [dict(test=t, reason="runs changed code", depth=depth.get(t), confidence=1.0)
             for t in sorted(ran, key=lambda t: (depth.get(t) is None, depth.get(t) or 0, t))]
    maybe = {}
    for test, fn in db.execute("SELECT test, function FROM covers"):
        if test not in ran and fn in downstream:
            maybe[test] = max(maybe.get(test, 0), downstream[fn])
    tests += [dict(test=t, reason="runs code that depends on it", depth=None, confidence=round(s, 3))
              for t, s in sorted(maybe.items(), key=lambda x: (-x[1], x[0]))]
    traced = {k for (k,) in db.execute("SELECT DISTINCT function FROM covers")}
    kinds = dict(db.execute("SELECT key, kind FROM dots"))

    def up_to(k):
        return rollup(db, k, level) if level != "line" else k

    roll = {}
    for k, s in downstream.items():
        r = up_to(k)
        if r in {up_to(c) for c in changed}:
            continue
        if s > roll.get(r, (0,))[0]:
            roll[r] = (s, up_to(via[k]), kind_of[k])
    down = [dict(key=k, confidence=round(s, 3), via=v, source=src)
            for k, (s, v, src) in sorted(roll.items(), key=lambda x: (-x[1][0], x[0]))]
    reach = sorted(changed | set(downstream))
    public = sorted({up_to(k) for k in reach if kinds.get(k) == "function" and is_public(k)})
    untested = sorted({up_to(k) for k in reach if kinds.get(k) == "function" and k not in traced
                       and not any(x in traced for x in expand(db, k))})
    return dict(level=level, changed=sorted({up_to(k) for k in changed}), tests=tests, downstream=down,
                public=public, untested=untested, cutoff=cutoff)
