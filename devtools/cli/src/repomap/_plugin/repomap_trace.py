"""pytest plugin: record what each test executes in one package, plus value summaries.

Load with:  REPOMAP_ROOT=src/werkzeug REPOMAP_OUT=map.json python -m pytest -p repomap_trace ...
(the directory holding this file must be on PYTHONPATH).

Uses sys.monitoring (Python 3.12+). For each test it records the package lines executed, including
the lines run by class-, module- and session-scoped fixtures it uses (setup_class too): those run once,
inside whichever test needs them first, but every test that uses them depends on them. Each line
event is disabled after its first hit and re-armed at the next test, so the cost is one callback per
distinct line per test. For every package function it keeps a bounded summary of argument and return
values: types seen and whether empty / zero / negative / None / bool values ever appeared, and for
strings which character classes appeared (non-ASCII, sign or radix prefix, quotes, control characters,
an empty field between separators).

Output JSON:
  tests:     {nodeid: {"outcome": pass|fail|skip, "lines": ["rel/file.py:123", ...]}}
  edges:     [[caller, callee], ...] with REPOMAP_EDGES=1; caller "<test>" for calls from test code
  functions: {"rel/file.py:Qual.name": {"first": line, "calls": n, "args": {name: summary},
              "returns": summary, "raises": {exc type: n}}}
  calls:     {nodeid: {"steps": n, "truncated": bool,
              "rows": [[seq, depth, parent_seq, function, args, ret, raised], ...]}}
             Every package function call in the test, in order (setup, call and teardown together). seq counts
             from 0, depth 1 means called from test code (or from code outside the package), parent_seq is the
             caller's row. args maps name -> {"type", "repr"}, ret is {"type", "repr"}, raised is the exception
             type that left the function, or null. self/cls keep only their type. At most REPOMAP_STEPS rows per
             test (default 500, 0 turns call order off); REPOMAP_VALUES=0 drops args/ret; REPOMAP_REPR sets the
             repr length (default 80). Values come from your tests, so they can hold fixture secrets: keep the
             output local.
"""
import json, os, reprlib, sys
from collections import defaultdict

import pytest

mon = sys.monitoring
TOOL = 4  # free tool id: leaves debugger, coverage and profiler slots to the code under test
E = mon.events
ROOT = os.path.abspath(os.environ.get("REPOMAP_ROOT", "src"))
BASE = os.path.dirname(ROOT)
OUT = os.environ.get("REPOMAP_OUT", "repomap.json")
EDGES = os.environ.get("REPOMAP_EDGES") == "1"  # also record caller -> callee edges (slower)
SAMPLES = int(os.environ.get("REPOMAP_SAMPLES", "30"))  # calls per function per test that get value summaries
STEPS = int(os.environ.get("REPOMAP_STEPS", "500"))  # call-order rows kept per test; 0 = don't record call order
VALUES = os.environ.get("REPOMAP_VALUES", "1") != "0"  # store a short repr of each call's arguments and result
REPR_LEN = int(os.environ.get("REPOMAP_REPR", "80"))
_repr = reprlib.Repr(maxlevel=2, maxstring=REPR_LEN, maxother=REPR_LEN, maxlist=6, maxtuple=6, maxdict=6,
                     maxset=6, maxfrozenset=6, maxdeque=6, maxarray=6, maxlong=40)


def tname(v):
    t = type(v)
    return t.__qualname__ if t.__module__ == "builtins" else f"{t.__module__}.{t.__qualname__}"


def short(v):
    """{"type", "repr"} of a value, cut to REPR_LEN characters; a repr that fails is recorded as such."""
    try:
        r = _repr.repr(v)
    except Exception as e:
        r = f"<repr failed: {type(e).__name__}>"
    if len(r) > REPR_LEN:
        r = r[:REPR_LEN - 1] + "…"
    return {"type": tname(v), "repr": r}


def summary():
    return {"types": defaultdict(int), "flags": defaultdict(int)}


SEPS = ",;:/=&@|"
QUOTES = "\"'"


def note_str(f, v):
    """Character classes of a non-empty string: what kind of text the tests actually fed in."""
    if not v.isascii():
        f["non_ascii"] += 1
        if any(c.isdigit() and not c.isascii() for c in v[:200]):
            f["non_ascii_digit"] += 1
    if v[0] in "+-":
        f["sign_prefix"] += 1
    if v[:2].lower() in ("0x", "0o", "0b"):
        f["radix_prefix"] += 1
    if any(c in QUOTES for c in v[:200]):
        f["quote"] += 1
    if any(ord(c) < 32 or ord(c) == 127 for c in v[:200]):
        f["control_char"] += 1
    # an empty field: a separator at either end, or two separators with nothing between them
    w = v[:200].replace(" ", "")
    if w and (w[0] in SEPS or w[-1] in SEPS or any(a in SEPS and b in SEPS for a, b in zip(w, w[1:]))):
        f["empty_field"] += 1


def note(s, v):
    t = type(v)
    s["types"][t.__qualname__ if t.__module__ == "builtins" else f"{t.__module__}.{t.__qualname__}"] += 1
    f = s["flags"]
    try:
        if v is None:
            f["none"] += 1
        elif isinstance(v, bool):
            f["true" if v else "false"] += 1
        elif isinstance(v, (int, float)):
            f["zero" if v == 0 else "negative" if v < 0 else "positive"] += 1
            if isinstance(v, float) and v != v:
                f["nan"] += 1
        elif isinstance(v, (str, bytes, list, tuple, dict, set, frozenset)):
            f["empty" if len(v) == 0 else "nonempty"] += 1
            if isinstance(v, (str, bytes)) and v and (v.strip() != v):
                f["edge_whitespace"] += 1
            if isinstance(v, str) and v:
                note_str(f, v)
    except Exception:
        f["unsummarisable"] += 1


class Recorder:
    def __init__(self):
        self.tests = {}
        self.funcs = {}
        self.lines = None
        self.calls = defaultdict(int)
        self.mine = {}
        self.edges = set()
        self.shared = defaultdict(set)  # (fixture name, baseid) -> lines its setup ran
        self.uses = {}  # nodeid -> shared fixtures the test depends on
        self.calls_out = {}  # nodeid -> {"steps", "truncated", "rows"}
        self.rows = None  # call-order rows of the running test, or None outside tests
        self.steps = 0
        self.stack = []  # [code, seq] of package frames currently running, innermost last
        self.suspended = {}  # id(generator frame) -> seq, so a resumed generator continues its own row

    def is_mine(self, code):
        r = self.mine.get(code)
        if r is None:
            r = self.mine[code] = code.co_filename.startswith(ROOT)
        return r

    def key(self, code):
        return f"{os.path.relpath(code.co_filename, BASE)}:{code.co_qualname}"

    def func(self, code):
        k = self.key(code)
        f = self.funcs.get(k)
        if f is None:
            f = self.funcs[k] = {"first": code.co_firstlineno, "calls": 0, "args": {}, "returns": summary(),
                                 "raises": defaultdict(int)}
        return f

    def on_line(self, code, line):
        if not self.is_mine(code):
            return mon.DISABLE
        if self.lines is not None:
            self.lines.add(f"{os.path.relpath(code.co_filename, BASE)}:{line}")
        return mon.DISABLE

    def on_start(self, code, offset):
        if not self.is_mine(code):
            return mon.DISABLE
        f = self.func(code)
        f["calls"] += 1
        self.calls[code] += 1
        if EDGES:
            back = sys._getframe(1).f_back
            if back is not None:
                cc = back.f_code
                caller = self.key(cc) if self.is_mine(cc) else (
                    "<test>" if os.sep + "tests" + os.sep in cc.co_filename else None)
                if caller:
                    self.edges.add((caller, self.key(code)))
        frame = sys._getframe(1)
        names = code.co_varnames[:code.co_argcount + code.co_kwonlyargcount
                                  + bool(code.co_flags & 4) + bool(code.co_flags & 8)]
        if self.rows is not None:
            self.push(code, frame, names)
        if self.calls[code] > SAMPLES:
            return
        for n in names:
            if n in ("self", "cls"):
                continue
            note(f["args"].setdefault(n, summary()), frame.f_locals.get(n))

    # ---- call order ----

    def push(self, code, frame, names):
        seq = None
        if self.steps < STEPS:
            seq = len(self.rows)
            parent = next((s for _, s in reversed(self.stack) if s is not None), None)
            args = None
            if VALUES:
                args = {}
                for n in names:
                    v = frame.f_locals.get(n)
                    args[n] = {"type": tname(v), "repr": None} if n in ("self", "cls") else short(v)
            self.rows.append([seq, len(self.stack) + 1, parent, self.key(code), args, None, None])
        self.steps += 1
        self.stack.append([code, seq])

    def pop(self, code, value=None, exc=None, keep=False):
        """Leave the innermost frame running code. Frames above it that never reported an exit are dropped."""
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] is code:
                seq = self.stack[i][1]
                del self.stack[i:]
                if seq is not None:
                    row = self.rows[seq]
                    if exc is not None:
                        row[6] = type(exc).__name__
                    elif VALUES and not keep:
                        row[5] = short(value)
                return seq
        return None

    def on_yield(self, code, offset, value):
        if not self.is_mine(code):
            return mon.DISABLE
        if self.rows is not None:
            seq = self.pop(code, keep=True)
            if seq is not None:
                self.suspended[id(sys._getframe(1))] = seq

    def on_resume(self, code, offset):
        if not self.is_mine(code):
            return mon.DISABLE
        if self.rows is None:
            return
        seq = self.suspended.pop(id(sys._getframe(1)), None)
        if seq is not None:
            self.stack.append([code, seq])

    def on_unwind(self, code, offset, exc):
        if self.rows is not None and self.is_mine(code):
            self.pop(code, exc=exc)

    def on_return(self, code, offset, value):
        if not self.is_mine(code):
            return mon.DISABLE
        if self.rows is not None:
            self.pop(code, value)
        if self.calls[code] <= SAMPLES:
            note(self.func(code)["returns"], value)

    def on_raise(self, code, offset, exc):
        if self.is_mine(code) and self.calls[code] <= SAMPLES:
            self.func(code)["raises"][type(exc).__name__] += 1

    def start(self):
        mon.use_tool_id(TOOL, "repomap")
        mon.register_callback(TOOL, E.LINE, self.on_line)
        mon.register_callback(TOOL, E.PY_START, self.on_start)
        mon.register_callback(TOOL, E.PY_RETURN, self.on_return)
        mon.register_callback(TOOL, E.RAISE, self.on_raise)
        events = E.LINE | E.PY_START | E.PY_RETURN | E.RAISE
        if STEPS:
            mon.register_callback(TOOL, E.PY_UNWIND, self.on_unwind)
            mon.register_callback(TOOL, E.PY_YIELD, self.on_yield)
            mon.register_callback(TOOL, E.PY_RESUME, self.on_resume)
            events |= E.PY_UNWIND | E.PY_YIELD | E.PY_RESUME
        mon.set_events(TOOL, events)

    def begin_test(self):
        self.lines = set()
        self.calls.clear()
        if STEPS:
            self.rows, self.steps, self.stack, self.suspended = [], 0, [], {}
        mon.restart_events()

    def end_test(self, nodeid, outcome):
        t = self.tests.setdefault(nodeid, {"outcome": "pass", "lines": set()})
        t["lines"] |= self.lines or set()
        for key in self.uses.get(nodeid, ()):
            t["lines"] |= self.shared.get(key, set())
        if outcome != "pass" and t["outcome"] == "pass":  # setup/teardown failures count too
            t["outcome"] = outcome
        self.lines = None
        if self.rows is not None:
            # the same list keeps growing if teardown still runs after a failed call phase
            self.calls_out[nodeid] = {"steps": self.steps, "truncated": self.steps > STEPS, "rows": self.rows}

    def dump(self):
        mon.set_events(TOOL, 0)
        mon.free_tool_id(TOOL)
        out = {"root": os.path.relpath(ROOT, BASE),
               "tests": {k: {"outcome": v["outcome"], "lines": sorted(v["lines"])} for k, v in self.tests.items()},
               "functions": self.funcs, "edges": sorted(self.edges), "calls": self.calls_out}
        with open(OUT, "w") as fh:
            json.dump(out, fh, default=dict)


REC = Recorder()


def pytest_configure(config):
    REC.start()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    REC.begin_test()
    info = getattr(item, "_fixtureinfo", None)
    keys = []
    for name in getattr(item, "fixturenames", ()):
        defs = info.name2fixturedefs.get(name) if info else None
        if defs and defs[-1].scope != "function":
            keys.append((name, defs[-1].baseid))
    REC.uses[item.nodeid] = keys


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    if fixturedef.scope == "function" or REC.lines is None:
        yield
        return
    outer, REC.lines = REC.lines, set()
    mon.restart_events()  # lines the test already hit must fire again to be credited to the fixture
    try:
        yield
    finally:
        REC.shared[(fixturedef.argname, fixturedef.baseid)] |= REC.lines
        REC.lines = outer | REC.lines


def pytest_runtest_logreport(report):
    outcome = "fail" if report.failed else "skip" if report.skipped and report.when == "setup" else "pass"
    if report.when == "teardown" or report.failed:
        REC.end_test(report.nodeid, outcome)
        REC.lines = set() if report.when != "teardown" else None
        if report.when == "teardown":
            REC.rows = None
    elif report.when == "setup" and report.skipped:
        REC.end_test(report.nodeid, "skip")
        REC.rows = None


def pytest_unconfigure(config):
    REC.dump()
