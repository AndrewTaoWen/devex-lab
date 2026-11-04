"""Ask a traced map what the test suite actually did, for an agent or a person.

usage (also available as `repomap query KIND [ARG]`, which reads .repomap/memory.json):
  query.py MAP fn NAME          value summary for functions whose key contains NAME
  query.py MAP callers NAME     who calls it (needs a map traced with REPOMAP_EDGES=1)
  query.py MAP tests FILE:LINE  which tests run that line
  query.py MAP gaps [MODULE] [--public] [--limit N] [--basic]
                                arguments the tests never fed an edge value: empty, zero, negative,
                                False, and for strings non-ASCII, a sign or 0x prefix, quotes, control
                                characters, surrounding whitespace or an empty field between separators.
                                --public drops private and nested functions; --limit keeps the N most
                                called; --basic uses only the empty/zero/negative probes.

Add --json anywhere for machine-readable output.

The gaps view is the one meant for hardening agents: an argument that takes strings but was never
given an empty one, or numbers but never zero or a negative, is untested territory.
"""
import json, sys

BASIC = {  # type seen -> flags worth having seen at least once
    "str": ("empty",), "bytes": ("empty",), "list": ("empty",), "tuple": ("empty",), "dict": ("empty",),
    "int": ("zero", "negative"), "float": ("zero", "negative"),
}
# richer probes; each is used only if the map recorded it somewhere (older maps lack most of them)
RICH = {
    "str": ("empty", "non_ascii", "non_ascii_digit", "sign_prefix", "radix_prefix", "quote", "control_char",
            "edge_whitespace", "empty_field"),
    "bytes": ("empty", "edge_whitespace"),
    "bool": ("false",),
}
PROBES = BASIC


def fmt_summary(s):
    types = ", ".join(f"{t}×{n}" for t, n in sorted(s["types"].items(), key=lambda x: -x[1]))
    flags = ", ".join(f"{f}×{n}" for f, n in sorted(s["flags"].items()))
    return f"{types or 'never seen'}" + (f"  [{flags}]" if flags else "")


def gaps_of(s, probes=None):
    missing = []
    for t in s["types"]:
        for f in (probes or PROBES).get(t, ()):
            if not s["flags"].get(f) and f not in missing:
                missing.append(f)
    return missing


def rich_probes(F):
    seen = {fl for f in F.values() for s in f["args"].values() for fl in s["flags"]}
    out = {t: list(v) for t, v in BASIC.items()}
    for t, fls in RICH.items():
        out[t] = list(dict.fromkeys(out.get(t, []) + [f for f in fls if f in seen or f == "empty"]))
    return out


def is_public(key):
    qual = key.split(":", 1)[1]
    parts = qual.split(".")
    if "<locals>" in parts or any(p.startswith("<") for p in parts):
        return False
    mod = key.split(":", 1)[0].split("/")
    if any(x.startswith("_") and x != "__init__.py" for x in mod):
        return False
    return not any(p.startswith("_") and p not in ("__init__", "__call__") for p in parts)


def run(m, cmd, arg="", public=False, limit=None, basic=False):
    """The query as data: what --json prints and what the MCP server returns.
    public, limit and basic apply to gaps only (see the usage above)."""
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
        if m.get("prefix") and arg.startswith(m["prefix"]):  # accept src/pkg/x.py:12 as well as pkg/x.py:12
            arg = arg[len(m["prefix"]):]
        return [t for t, v in tm.items() if arg in v["lines"]]
    if cmd == "gaps":
        probes = BASIC if basic else rich_probes(F)
        rows = []
        for k in F:
            if (arg and arg not in k) or (public and not is_public(k)):
                continue
            g = {a: gg for a, s in F[k]["args"].items() if a not in ("self", "cls") and (gg := gaps_of(s, probes))}
            if g:
                rows.append(dict(function=k, calls=F[k]["calls"], never=g))
        rows.sort(key=lambda r: (-r["calls"], r["function"]) if limit else r["function"])
        return rows[:limit]
    raise ValueError(f"unknown query {cmd!r}")


def main():
    argv = sys.argv[1:]
    as_json = "--json" in argv
    flags = {a for a in argv if a in ("--public", "--basic")}
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    if limit is not None:
        del argv[argv.index("--limit"):argv.index("--limit") + 2]
    argv = [a for a in argv if a not in flags and a != "--json"]
    if len(argv) < 2 or argv[1] not in ("fn", "callers", "tests", "gaps"):
        sys.exit(__doc__)
    m = json.load(open(argv[0]))
    cmd, arg = argv[1], (argv[2] if len(argv) > 2 else "")
    show(run(m, cmd, arg, public="--public" in flags, limit=limit, basic="--basic" in flags), cmd, as_json)


def show(res, cmd, as_json=False):
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
