#!/usr/bin/env python3
"""Ask a traced map what the test suite actually did, for an agent or a person.

usage:
  query.py MAP fn NAME          value summary for functions whose key contains NAME
  query.py MAP callers NAME     who calls it (needs a map traced with REPOMAP_EDGES=1)
  query.py MAP tests FILE:LINE  which tests run that line
  query.py MAP gaps [MODULE] [--public] [--limit N] [--basic]
                                arguments the tests never fed an edge value: empty, zero, negative,
                                False, and for strings non-ASCII, a sign or 0x prefix, quotes, control
                                characters, surrounding whitespace or an empty field between separators.
                                --public drops private and nested functions; --limit keeps the N most
                                called; --basic uses only the empty/zero/negative probes.

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


def main():
    argv = sys.argv[1:]
    flags = {a for a in argv if a in ("--public", "--basic")}
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    if limit is not None:
        del argv[argv.index("--limit"):argv.index("--limit") + 2]
    argv = [a for a in argv if a not in flags]
    m = json.load(open(argv[0]))
    cmd, arg = argv[1], (argv[2] if len(argv) > 2 else "")
    F = m["functions"]
    if cmd == "fn":
        for k in sorted(x for x in F if arg in x):
            f = F[k]
            print(f"{k}  (line {f['first']}, {f['calls']} calls)")
            for a, s in f["args"].items():
                g = gaps_of(s)
                print(f"  {a}: {fmt_summary(s)}" + (f"  never: {', '.join(g)}" if g else ""))
            print(f"  -> {fmt_summary(f['returns'])}")
            if f["raises"]:
                print(f"  raises: {dict(f['raises'])}")
    elif cmd == "callers":
        for caller, callee in m.get("edges", []):
            if arg in callee:
                print(f"{caller} -> {callee}")
    elif cmd == "tests":
        for t, v in m["tests"].items():
            if arg in v["lines"]:
                print(t)
    elif cmd == "gaps":
        probes = BASIC if "--basic" in flags else rich_probes(F)
        rows = []
        for k in F:
            if (arg and arg not in k) or ("--public" in flags and not is_public(k)):
                continue
            out = [f"{a} never {'/'.join(g)}" for a, s in F[k]["args"].items()
                   if a not in ("self", "cls") and (g := gaps_of(s, probes))]
            if out:
                rows.append((F[k]["calls"], k, out))
        rows.sort(key=lambda r: (-r[0], r[1]) if limit else r[1])
        for calls, k, out in rows[:limit]:
            print(f"{k}: " + "; ".join(out))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
