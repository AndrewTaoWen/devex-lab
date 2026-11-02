#!/usr/bin/env python3
"""Ask a traced map what the test suite actually did, for an agent or a person.

usage:
  query.py MAP fn NAME          value summary for functions whose key contains NAME
  query.py MAP callers NAME     who calls it (needs a map traced with REPOMAP_EDGES=1)
  query.py MAP tests FILE:LINE  which tests run that line
  query.py MAP gaps [MODULE]    arguments never exercised with empty / zero / negative / None values

Add --json anywhere for machine-readable output.

The gaps view is the one meant for hardening agents: an argument that takes strings but was never
given an empty one, or numbers but never zero or a negative, is untested territory.
"""
import json, sys

PROBES = {  # type seen -> flags worth having seen at least once
    "str": ("empty",), "bytes": ("empty",), "list": ("empty",), "tuple": ("empty",), "dict": ("empty",),
    "int": ("zero", "negative"), "float": ("zero", "negative"),
}


def fmt_summary(s):
    types = ", ".join(f"{t}×{n}" for t, n in sorted(s["types"].items(), key=lambda x: -x[1]))
    flags = ", ".join(f"{f}×{n}" for f, n in sorted(s["flags"].items()))
    return f"{types or 'never seen'}" + (f"  [{flags}]" if flags else "")


def gaps_of(s, optional=False):
    missing = []
    for t in s["types"]:
        for f in PROBES.get(t, ()):
            if not s["flags"].get(f) and f not in missing:
                missing.append(f)
    return missing


def run(m, cmd, arg=""):
    """The query as data: what --json prints and what the MCP server returns."""
    F = m["functions"]
    if cmd == "fn":
        out = []
        for k in sorted(x for x in F if arg in x):
            f = F[k]
            out.append(dict(function=k, line=f["first"], calls=f["calls"],
                            args={a: dict(s, never=gaps_of(s)) for a, s in f["args"].items()},
                            returns=f["returns"], raises=dict(f["raises"] or {})))
        return out
    if cmd == "callers":
        return [dict(caller=c, callee=e) for c, e in m.get("edges", []) if arg in e]
    if cmd == "tests":
        tm = m.get("tests_map") or m.get("tests") or {}
        return [t for t, v in tm.items() if arg in v["lines"]]
    if cmd == "gaps":
        out = []
        for k in sorted(F):
            if arg and arg not in k:
                continue
            g = {a: gg for a, s in F[k]["args"].items() if (gg := gaps_of(s))}
            if g:
                out.append(dict(function=k, never=g))
        return out
    raise ValueError(f"unknown query {cmd!r}")


def main():
    argv = [x for x in sys.argv[1:] if x != "--json"]
    as_json = len(argv) != len(sys.argv) - 1
    if len(argv) < 2 or argv[1] not in ("fn", "callers", "tests", "gaps"):
        sys.exit(__doc__)
    m = json.load(open(argv[0]))
    cmd, arg = argv[1], (argv[2] if len(argv) > 2 else "")
    res = run(m, cmd, arg)
    if as_json:
        print(json.dumps(res, indent=1))
    elif cmd == "fn":
        for f in res:
            print(f"{f['function']}  (line {f['line']}, {f['calls']} calls)")
            for a, s in f["args"].items():
                print(f"  {a}: {fmt_summary(s)}" + (f"  never: {', '.join(s['never'])}" if s["never"] else ""))
            print(f"  -> {fmt_summary(f['returns'])}")
            if f["raises"]:
                print(f"  raises: {f['raises']}")
    elif cmd == "callers":
        for e in res:
            print(f"{e['caller']} -> {e['callee']}")
    elif cmd == "tests":
        print("\n".join(res))
    else:
        for f in res:
            print(f"{f['function']}: " + "; ".join(f"{a} never {'/'.join(g)}" for a, g in f["never"].items()))


if __name__ == "__main__":
    main()
