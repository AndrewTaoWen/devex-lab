#!/usr/bin/env python3
"""Scorecard for the code graph's impact (`repomap impact --graph`), on injected faults.

usage: (from the repo root, with .repomap/memory.json and .repomap/graph.db)
  graphbench.py truth --n 50 --seed 1 --out graphbench.json   inject faults, re-trace, record what changed
  graphbench.py score graphbench.json [MUTBENCH.json]        score the graph against those records

`truth` reuses mutbench's faults (flip a comparison, nudge a constant, return None ...). For each one it runs
the whole suite under the tracer with call order and values, then compares every package function's calls
with a clean run, test by test. A function "behaved differently" if, in some test, the set of (arguments,
result, exception) it was seen with changed. Two clean runs are compared first, and (test, function) pairs
that differ between them (timestamps, temp paths, random ids) are left out. Those functions, minus the one that was edited, are the true downstream of the fault: the
code that a reviewer should have looked at. The broken tests are kept too.

`score` reports, for the graph's downstream bucket at the default cutoff:
  where        how the truly affected functions sit relative to the change: among its callers (the direction
               the graph walks), among its callees (it passed them bad arguments), or elsewhere (state it left
               behind, read by later calls in the same test)
  recall       share of truly affected functions the graph listed
  precision    share of listed functions that really behaved differently
                (low precision is expected: a caller that passes the bad value along unchanged, or a fault the
                tests never reach, counts against it; the point is to see where the confidence cut sits)
and for tests:
  safety       share of broken tests among the tests the graph says to run (must stay at 100%)
  first fail   how far down the graph's closest-first list the first broken test sits, against the same
               tests in plain alphabetical order: lower means a fault shows up sooner
With MUTBENCH.json (the recorded broken tests from mutbench.py), test safety is also scored on those faults.
"""
import argparse, ast, json, os, random, re, statistics, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(HERE))
from repomap import cli as rm, graph  # noqa: E402
import mutbench  # noqa: E402

ADDR = re.compile(r"0x[0-9a-fA-F]+")


def behaviour(calls):
    """(test, function) -> set of (args, ret, raised) seen, with memory addresses blanked out."""
    out = {}
    for test, c in calls.items():
        for _, _, _, fn, args, ret, raised in c["rows"]:
            sig = ADDR.sub("0x?", json.dumps([args, ret, raised], sort_keys=True))
            out.setdefault((test, fn), set()).add(sig)
    return out


def differs(base, now, noise=frozenset()):
    """Functions whose behaviour changed in some test, including calls that stopped or started. Pairs in noise
    (a test and function that differ between two clean runs: times, temp paths, random ids) are ignored."""
    return sorted({fn for (t, fn) in base.keys() | now.keys()
                   if (t, fn) not in noise and base.get((t, fn)) != now.get((t, fn))})


def noisy(a, b):
    return frozenset(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))


def rollup_key(k):
    """Nested code objects count as the function that holds them; module code as the module."""
    return k.split(".<locals>.", 1)[0]


def clear_pyc(path):
    """Drop cached bytecode for a source file. A same-size edit restored within the same second keeps the old
    mtime and size, so Python would otherwise keep running the mutant's bytecode after the file is restored."""
    for p in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
        p.unlink(missing_ok=True)


def run_trace(m, td, name):
    m = dict(m, trace_opts=dict(m.get("trace_opts", {}), steps=100_000, values=True))
    raw = rm.trace(m, [m["tests"]], Path(td) / f"{name}.json")
    failed = {t for t, v in raw["tests"].items() if v["outcome"] == "fail"}
    return raw.get("calls", {}), failed


def truth(a):
    m = rm.load()
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # the traced runs inherit it: no bytecode from mutants
    for f in rm.package_files(m):
        clear_pyc(Path(f))
    rng = random.Random(a.seed)
    out = Path(a.out)
    results = json.loads(out.read_text()) if out.exists() else []
    done = {r["function"] for r in results}
    with tempfile.TemporaryDirectory() as td:  # two clean runs: what differs between them is noise
        base_calls, base_failed = run_trace(m, td, "base")
        again_calls, again_failed = run_trace(m, td, "again")
    base = behaviour(base_calls)
    noise = noisy(base, behaviour(again_calls))
    base_failed |= again_failed
    print(f"clean runs differ in {len(noise)} (test, function) pairs; those are ignored", flush=True)
    keys = [k for k in m["functions"] if "<" not in k.split(":", 1)[1]]
    rng.shuffle(keys)
    for k in keys:
        if len(results) >= a.n:
            break
        if k in done:
            continue
        rel, q = k.split(":", 1)
        path = Path(m["prefix"] + rel)
        src = path.read_text()
        tree = ast.parse(src)
        node = next((n for s, e, qq in rm.functions(src) if qq == q
                     for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.lineno <= e and (n.end_lineno or 0) >= s and n.name == q.split(".")[-1]), None)
        cands = mutbench.candidates(node) if node else []
        if not cands:
            continue
        target, text, kind = rng.choice(cands)
        t0 = time.time()
        try:
            path.write_text(mutbench.splice(src, target, text))
            clear_pyc(path)
            with tempfile.TemporaryDirectory() as td:
                calls, failed = run_trace(m, td, "mutant")
        except SystemExit:  # the mutant broke collection: no behaviour to compare
            continue
        finally:
            path.write_text(src)
            clear_pyc(path)
        changed = sorted({rollup_key(f) for f in differs(base, behaviour(calls), noise)} - {k})
        r = dict(function=k, kind=kind, line=target.lineno, mutant=mutbench.splice(src, target, text).splitlines()
                 [target.lineno - 1].strip(), behaved_differently=changed,
                 broken_tests=sorted(failed - base_failed), seconds=round(time.time() - t0, 1))
        results.append(r)
        out.write_text(json.dumps(results, indent=1))
        print(f"{len(results):3d} {k[:60]:60s} {kind:15s} downstream {len(changed):3d} broke "
              f"{len(r['broken_tests']):3d}", flush=True)


def first_fail(order, broken):
    """1-based position of the first broken test in a run order, or None if none of them is broken."""
    return next((i for i, t in enumerate(order, 1) if t in broken), None)


def reach(db, start, forward):
    """Every function reachable from start along effective call arrows: callees if forward, else callers."""
    nxt = {}
    for a, b in db.execute("SELECT src, dst FROM effective_arrows WHERE kind IN ('calls', 'calls_by_name')"):
        a, b = rollup_key(a), rollup_key(b)
        (nxt.setdefault(a, set()).add(b)) if forward else nxt.setdefault(b, set()).add(a)
    seen, todo = set(), [start]
    while todo:
        for x in nxt.get(todo.pop(), ()):
            if x not in seen:
                seen.add(x)
                todo.append(x)
    return seen - {start}


_DOTS = {}


def package_dots(db):
    if id(db) not in _DOTS:
        _DOTS[id(db)] = {k for (k,) in db.execute("SELECT key FROM dots")}
    return _DOTS[id(db)]


def score_one(db, r, suite):
    imp = graph.impact(db, {r["function"]})
    listed = {d["key"] for d in imp["downstream"]}
    # package code only: modules a test writes to a temp dir and imports can show up in the trace
    truth_ = {f for f in r["behaved_differently"] if f in package_dots(db)}
    sure = [t["test"] for t in imp["tests"] if t["reason"] == "runs changed code"]
    broken = set(r["broken_tests"])
    ranked, plain = first_fail(sure, broken), first_fail(sorted(sure), broken)
    callers, callees = reach(db, r["function"], False), reach(db, r["function"], True)
    # where the truly affected code sits relative to the change: the graph walks callers only
    where = dict(callers=len(truth_ & callers), callees=len(truth_ & (callees - callers)),
                 elsewhere=len(truth_ - callers - callees))
    return dict(function=r["function"], kind=r["kind"], truth=len(truth_), listed=len(listed), where=where,
                hit=len(truth_ & listed), missed=sorted(truth_ - listed), extra=sorted(listed - truth_),
                broken=len(broken), broken_selected=len(broken & set(sure)), selected=len(sure), suite=suite,
                first_fail_ranked=ranked, first_fail_plain=plain)


def summarize(rows):
    with_truth = [x for x in rows if x["truth"]]
    hit, tru, lst = (sum(x[k] for x in rows) for k in ("hit", "truth", "listed"))
    live = [x for x in rows if x["broken"]]
    both = [x for x in live if x["first_fail_ranked"] and x["first_fail_plain"]]
    med = lambda v: statistics.median(v) if v else None  # noqa: E731
    return dict(
        faults=len(rows), with_downstream=len(with_truth),
        truth_in_callers=sum(x["where"]["callers"] for x in rows),
        truth_in_callees=sum(x["where"]["callees"] for x in rows),
        truth_elsewhere=sum(x["where"]["elsewhere"] for x in rows),
        downstream_recall=round(hit / tru, 3) if tru else None,
        downstream_precision=round(hit / lst, 3) if lst else None,
        downstream_recall_median=med([x["hit"] / x["truth"] for x in with_truth]),
        broken_tests=sum(x["broken"] for x in live), broken_selected=sum(x["broken_selected"] for x in live),
        selected_share_median=med([x["selected"] / x["suite"] for x in live]),
        first_fail_ranked_median=med([x["first_fail_ranked"] for x in both]),
        first_fail_plain_median=med([x["first_fail_plain"] for x in both]),
        ranked_sooner=sum(x["first_fail_ranked"] < x["first_fail_plain"] for x in both),
        ranked_later=sum(x["first_fail_ranked"] > x["first_fail_plain"] for x in both))


def score(a):
    results = json.loads(Path(a.results).read_text())
    db = graph.connect()
    suite = db.execute("SELECT COUNT(*) FROM tests").fetchone()[0]
    rows = [score_one(db, r, suite) for r in results]
    s = summarize(rows)
    if a.mutbench:
        mb = json.loads(Path(a.mutbench).read_text())
        tot = sel = 0
        for r in mb:
            imp = graph.impact(db, {r["function"]})
            sure = {mutbench.norm(t["test"]) for t in imp["tests"] if t["reason"] == "runs changed code"}
            k = {t for t in r["killed_tests"] if not t.startswith("::")}
            tot, sel = tot + len(k), sel + len(k & sure)
        s.update(mutbench_faults=len(mb), mutbench_broken=tot, mutbench_broken_selected=sel)
    res = dict(summary=s, faults=rows)
    if a.json:
        print(json.dumps(res, indent=1))
        return res
    print(f"{s['faults']} faults, {s['with_downstream']} changed the behaviour of other functions")
    print(f"  downstream recall {s['downstream_recall']}, precision {s['downstream_precision']} "
          f"(per-fault median recall {s['downstream_recall_median']})")
    print(f"  truly affected functions: {s['truth_in_callers']} among callers (what the graph walks), "
          f"{s['truth_in_callees']} among callees (bad arguments passed down), {s['truth_elsewhere']} elsewhere "
          f"(shared state, later calls in the same test)")
    print(f"  broken tests selected {s['broken_selected']} of {s['broken_tests']}, "
          f"median share of suite {s['selected_share_median']}")
    print(f"  first broken test at position {s['first_fail_ranked_median']} (closest first) vs "
          f"{s['first_fail_plain_median']} (alphabetical); sooner on {s['ranked_sooner']}, "
          f"later on {s['ranked_later']}")
    if "mutbench_faults" in s:
        print(f"  mutbench: {s['mutbench_broken_selected']} of {s['mutbench_broken']} broken tests selected "
              f"over {s['mutbench_faults']} faults")
    for x in rows:
        if x["missed"]:
            print(f"    missed {len(x['missed'])} of {x['truth']} for {x['function']} ({x['kind']}): "
                  f"{', '.join(x['missed'][:3])}")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("truth")
    t.add_argument("--n", type=int, default=50)
    t.add_argument("--seed", type=int, default=1)
    t.add_argument("--out", default="graphbench.json")
    s = sub.add_parser("score")
    s.add_argument("results")
    s.add_argument("mutbench", nargs="?")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    truth(a) if a.cmd == "truth" else score(a)


if __name__ == "__main__":
    main()
