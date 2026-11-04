"""Step 0b: were the lines each future bug fix touched ever run by the snapshot's tests?

usage: S=<dir with bugs.json> coverage_check.py REPO CLONE PKG_PREFIX MAP.json SNAPSHOT_SHA

For each bug commit, takes the executable statements its diff removes or edits (or, for a pure
insertion, the statement it lands after), maps them from the commit's parent back to the snapshot
with difflib, and checks the map's line sets. Also prints the base rate: the share of statements
inside package functions that no test runs.
"""
import ast, difflib, glob, json, os, re, subprocess, sys

def git(clone, *a):
    return subprocess.run(["git", "-C", clone, *a], capture_output=True, text=True).stdout


def stmts(src, inside_functions=False):
    tree = ast.parse(src)
    out = set()
    roots = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] if inside_functions else [tree]
    for r in roots:
        for n in ast.walk(r):
            if n is r or not isinstance(n, ast.stmt) or isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                continue
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
                continue
            out.add(n.lineno)
    return out


def to_snapshot(parent_src, snap_src):
    """parent line number -> snapshot line number, for lines unchanged since the snapshot"""
    a, b = parent_src.splitlines(), snap_src.splitlines()
    mp = {}
    for i, j, n in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(n):
            mp[i + k + 1] = j + k + 1
    return mp


def main():
    repo, clone, prefix, mapf, snap = sys.argv[1:6]
    bugs = json.load(open(os.environ["S"] + "/bugs.json"))[repo]
    m = json.load(open(mapf))
    hit = {l for t in m["tests"].values() for l in t["lines"]}

    def g(*a):
        return git(clone, *a)

    rows = []
    for c in bugs:
        full = g("rev-parse", c).strip()
        run = unrun = new = 0
        for f in g("diff", "--name-only", full + "^", full).split():
            if not f.startswith(prefix) or not f.endswith(".py"):
                continue
            parent = g("show", f"{full}^:{f}")
            if not parent:
                continue
            snap_src = g("show", f"{snap}:{f}")
            mp = to_snapshot(parent, snap_src) if snap_src else {}
            ex = stmts(parent)
            rel = f[len(prefix):] if prefix.endswith("/") else f
            for h in re.finditer(r"^@@ -(\d+)(?:,(\d+))?", g("diff", "-U0", full + "^", full, "--", f), re.M):
                s, n = int(h[1]), int(h[2] if h[2] is not None else 1)
                for L in (range(s, s + n) if n else [s]):
                    if L not in ex:
                        continue
                    if L not in mp:
                        new += 1
                    elif f"{rel}:{mp[L]}" in hit:
                        run += 1
                    else:
                        unrun += 1
        subj = g("log", "-1", "--format=%s", full).strip()
        rows.append(dict(commit=c, subject=subj, run=run, never_run=unrun, added_after_snapshot=new))
        print(f"{c} {subj[:48]:48} run {run}, never run {unrun}, added after snapshot {new}")

    root = prefix.rstrip("/") or "."
    tot = unc = 0
    for f in glob.glob(f"{clone}/{root}/**/*.py", recursive=True):
        rel = os.path.relpath(f, f"{clone}/{prefix}" if prefix.endswith("/") else clone)
        if "/tests/" in "/" + rel or rel.startswith("tests"):
            continue
        ex = stmts(open(f).read(), inside_functions=True)
        tot += len(ex)
        unc += sum(f"{rel}:{L}" not in hit for L in ex)
    print(f"base rate: {unc}/{tot} = {unc / tot:.0%} of statements in package functions never run")


if __name__ == "__main__":
    main()
