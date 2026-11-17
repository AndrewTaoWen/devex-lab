#!/usr/bin/env python3
"""Scorecard for the code graph's impact ('repomap impact --graph'), on injected faults.

usage: (from the repo root, with .repomap/memory.json and .repomap/graph.db)
  graphbench.py truth --n 50 --seed 1 --out graphbench.json   inject faults, re-trace, record what changed
  graphbench.py score graphbench.json [MUTBENCH.json]        score the graph against those records

truth reuses mutbench's faults (flip a comparison, nudge a constant, return None ...). For each one it runs
the whole suite under the tracer with call order and values, then compares every package function's calls
with a clean run, test by test. A function "behaved differently" if, in some test, the set of (arguments,
result, exception) it was seen with changed. Two clean runs are compared first, and (test, function) pairs
that differ between them (timestamps, temp paths, random ids) are left out. Those functions, minus the one that
was edited, are the true downstream of the fault: the code that a reviewer should have looked at. The broken tests
are kept too.

score reports, for the graph's downstream bucket at the default cutoff:
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
from repomap import cli as repomap_cli, graph  # noqa: E402
import mutbench  # noqa: E402

ADDR = re.compile(r"0x[0-9a-fA-F]+")


def behaviour(calls):
    """(test, function) -> set of (args, ret, raised) seen, with memory addresses blanked out."""
    out = {}
    for test, run in calls.items():
        for _, _, _, function, args, ret, raised in run["rows"]:
            signature = ADDR.sub("0x?", json.dumps([args, ret, raised], sort_keys=True))
            out.setdefault((test, function), set()).add(signature)
    return out


def differs(base, now, noise=frozenset()):
    """Functions whose behaviour changed in some test, including calls that stopped or started. Pairs in noise
    (a test and function that differ between two clean runs: times, temp paths, random ids) are ignored."""
    return sorted({function for (test, function) in base.keys() | now.keys()
                   if (test, function) not in noise and base.get((test, function)) != now.get((test, function))})


def noisy(first, second):
    return frozenset(pair for pair in first.keys() | second.keys() if first.get(pair) != second.get(pair))


def rollup_key(key):
    """Nested code objects count as the function that holds them; module code as the module."""
    return key.split(".<locals>.", 1)[0]


def clear_pyc(path):
    """Drop cached bytecode for a source file. A same-size edit restored within the same second keeps the old
    mtime and size, so Python would otherwise keep running the mutant's bytecode after the file is restored."""
    for cached in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
        cached.unlink(missing_ok=True)


def run_trace(memory, workdir, name):
    memory = dict(memory, trace_opts=dict(memory.get("trace_opts", {}), steps=100_000, values=True))
    raw = repomap_cli.trace(memory, [memory["tests"]], Path(workdir) / f"{name}.json")
    failed = {test for test, info in raw["tests"].items() if info["outcome"] == "fail"}
    return raw.get("calls", {}), failed


def truth(args):
    memory = repomap_cli.load()
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # the traced runs inherit it: no bytecode from mutants
    for file in repomap_cli.package_files(memory):
        clear_pyc(Path(file))
    rng = random.Random(args.seed)
    out = Path(args.out)
    results = json.loads(out.read_text()) if out.exists() else []
    done = {result["function"] for result in results}
    with tempfile.TemporaryDirectory() as workdir:  # two clean runs: what differs between them is noise
        base_calls, base_failed = run_trace(memory, workdir, "base")
        again_calls, again_failed = run_trace(memory, workdir, "again")
    base = behaviour(base_calls)
    noise = noisy(base, behaviour(again_calls))
    base_failed |= again_failed
    print(f"clean runs differ in {len(noise)} (test, function) pairs; those are ignored", flush=True)
    keys = [key for key in memory["functions"] if "<" not in key.split(":", 1)[1]]
    rng.shuffle(keys)
    for key in keys:
        if len(results) >= args.n:
            break
        if key in done:
            continue
        rel, qualname = key.split(":", 1)
        path = Path(memory["prefix"] + rel)
        source = path.read_text()
        tree = ast.parse(source)
        spans = [(start, end) for start, end, found in repomap_cli.functions(source) if found == qualname]
        name = qualname.split(".")[-1]
        node = next((node for start, end in spans for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
                     and node.lineno <= end and (node.end_lineno or 0) >= start), None)
        candidates = mutbench.candidates(node) if node else []
        if not candidates:
            continue
        target, text, kind = rng.choice(candidates)
        started = time.time()
        try:
            path.write_text(mutbench.splice(source, target, text))
            clear_pyc(path)
            with tempfile.TemporaryDirectory() as workdir:
                calls, failed = run_trace(memory, workdir, "mutant")
        except SystemExit:  # the mutant broke collection: no behaviour to compare
            continue
        finally:
            path.write_text(source)
            clear_pyc(path)
        changed = sorted({rollup_key(function) for function in differs(base, behaviour(calls), noise)} - {key})
        mutant_line = mutbench.splice(source, target, text).splitlines()[target.lineno - 1].strip()
        result = dict(function=key, kind=kind, line=target.lineno, mutant=mutant_line, behaved_differently=changed,
                      broken_tests=sorted(failed - base_failed), seconds=round(time.time() - started, 1))
        results.append(result)
        out.write_text(json.dumps(results, indent=1))
        print(f"{len(results):3d} {key[:60]:60s} {kind:15s} downstream {len(changed):3d} broke "
              f"{len(result['broken_tests']):3d}", flush=True)


def first_fail(order, broken):
    """1-based position of the first broken test in a run order, or None if none of them is broken."""
    return next((position for position, test in enumerate(order, 1) if test in broken), None)


def reach(db, start, forward):
    """Every function reachable from start along effective call arrows: callees if forward, else callers."""
    neighbours = {}
    for src, dst in db.execute("SELECT src, dst FROM effective_arrows WHERE kind IN ('calls', 'calls_by_name')"):
        src, dst = rollup_key(src), rollup_key(dst)
        if forward:
            neighbours.setdefault(src, set()).add(dst)
        else:
            neighbours.setdefault(dst, set()).add(src)
    seen, todo = set(), [start]
    while todo:
        for dot in neighbours.get(todo.pop(), ()):
            if dot not in seen:
                seen.add(dot)
                todo.append(dot)
    return seen - {start}


_DOTS = {}


def package_dots(db):
    if id(db) not in _DOTS:
        _DOTS[id(db)] = {key for (key,) in db.execute("SELECT key FROM dots")}
    return _DOTS[id(db)]


def score_one(db, fault, suite):
    result = graph.impact(db, {fault["function"]})
    listed = {dot["key"] for dot in result["downstream"]}
    # package code only: modules a test writes to a temp dir and imports can show up in the trace
    affected = {function for function in fault["behaved_differently"] if function in package_dots(db)}
    sure = [test["test"] for test in result["tests"] if test["reason"] == "runs changed code"]
    broken = set(fault["broken_tests"])
    ranked, plain = first_fail(sure, broken), first_fail(sorted(sure), broken)
    callers, callees = reach(db, fault["function"], False), reach(db, fault["function"], True)
    # where the truly affected code sits relative to the change: the graph walks callers only
    where = dict(callers=len(affected & callers), callees=len(affected & (callees - callers)),
                 elsewhere=len(affected - callers - callees))
    return dict(function=fault["function"], kind=fault["kind"], truth=len(affected), listed=len(listed), where=where,
                hit=len(affected & listed), missed=sorted(affected - listed), extra=sorted(listed - affected),
                broken=len(broken), broken_selected=len(broken & set(sure)), selected=len(sure), suite=suite,
                first_fail_ranked=ranked, first_fail_plain=plain)


def summarize(rows):
    with_truth = [row for row in rows if row["truth"]]
    hit, affected, listed = (sum(row[field] for row in rows) for field in ("hit", "truth", "listed"))
    live = [row for row in rows if row["broken"]]
    both = [row for row in live if row["first_fail_ranked"] and row["first_fail_plain"]]

    def median(values):
        return statistics.median(values) if values else None

    return dict(
        faults=len(rows), with_downstream=len(with_truth),
        truth_in_callers=sum(row["where"]["callers"] for row in rows),
        truth_in_callees=sum(row["where"]["callees"] for row in rows),
        truth_elsewhere=sum(row["where"]["elsewhere"] for row in rows),
        downstream_recall=round(hit / affected, 3) if affected else None,
        downstream_precision=round(hit / listed, 3) if listed else None,
        downstream_recall_median=median([row["hit"] / row["truth"] for row in with_truth]),
        broken_tests=sum(row["broken"] for row in live), broken_selected=sum(row["broken_selected"] for row in live),
        selected_share_median=median([row["selected"] / row["suite"] for row in live]),
        first_fail_ranked_median=median([row["first_fail_ranked"] for row in both]),
        first_fail_plain_median=median([row["first_fail_plain"] for row in both]),
        ranked_sooner=sum(row["first_fail_ranked"] < row["first_fail_plain"] for row in both),
        ranked_later=sum(row["first_fail_ranked"] > row["first_fail_plain"] for row in both))


def score(args):
    results = json.loads(Path(args.results).read_text())
    db = graph.connect()
    suite = db.execute("SELECT COUNT(*) FROM tests").fetchone()[0]
    rows = [score_one(db, fault, suite) for fault in results]
    stats = summarize(rows)
    if args.mutbench:
        mutbench_faults = json.loads(Path(args.mutbench).read_text())
        broken_total = broken_selected = 0
        for fault in mutbench_faults:
            result = graph.impact(db, {fault["function"]})
            sure = {mutbench.norm(test["test"]) for test in result["tests"] if test["reason"] == "runs changed code"}
            killed = {test for test in fault["killed_tests"] if not test.startswith("::")}
            broken_total, broken_selected = broken_total + len(killed), broken_selected + len(killed & sure)
        stats.update(mutbench_faults=len(mutbench_faults), mutbench_broken=broken_total,
                     mutbench_broken_selected=broken_selected)
    res = dict(summary=stats, faults=rows)
    if args.json:
        print(json.dumps(res, indent=1))
        return res
    print(f"{stats['faults']} faults, {stats['with_downstream']} changed the behaviour of other functions")
    print(f"  downstream recall {stats['downstream_recall']}, precision {stats['downstream_precision']} "
          f"(per-fault median recall {stats['downstream_recall_median']})")
    print(f"  truly affected functions: {stats['truth_in_callers']} among callers (what the graph walks), "
          f"{stats['truth_in_callees']} among callees (bad arguments passed down), "
          f"{stats['truth_elsewhere']} elsewhere (shared state, later calls in the same test)")
    print(f"  broken tests selected {stats['broken_selected']} of {stats['broken_tests']}, "
          f"median share of suite {stats['selected_share_median']}")
    print(f"  first broken test at position {stats['first_fail_ranked_median']} (closest first) vs "
          f"{stats['first_fail_plain_median']} (alphabetical); sooner on {stats['ranked_sooner']}, "
          f"later on {stats['ranked_later']}")
    if "mutbench_faults" in stats:
        print(f"  mutbench: {stats['mutbench_broken_selected']} of {stats['mutbench_broken']} broken tests selected "
              f"over {stats['mutbench_faults']} faults")
    for row in rows:
        if row["missed"]:
            print(f"    missed {len(row['missed'])} of {row['truth']} for {row['function']} ({row['kind']}): "
                  f"{', '.join(row['missed'][:3])}")
    return res


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    truth_parser = sub.add_parser("truth")
    truth_parser.add_argument("--n", type=int, default=50)
    truth_parser.add_argument("--seed", type=int, default=1)
    truth_parser.add_argument("--out", default="graphbench.json")
    score_parser = sub.add_parser("score")
    score_parser.add_argument("results")
    score_parser.add_argument("mutbench", nargs="?")
    score_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    truth(args) if args.cmd == "truth" else score(args)


if __name__ == "__main__":
    main()
