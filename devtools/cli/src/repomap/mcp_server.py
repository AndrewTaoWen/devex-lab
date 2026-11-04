"""repomap as an MCP server: change impact, test selection and recorded values as tools.

  repomap-mcp [--root DIR]

Speaks MCP over stdio. Start it from the repo root (or pass --root); it reads .repomap/memory.json there,
keeps it in memory, and re-reads it whenever the file changes (after a `refresh` from any client or shell).
Needs `pip install repomap[mcp]`.
"""
import argparse, functools, os, sys
from pathlib import Path
from types import SimpleNamespace

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations
except ImportError:  # pragma: no cover
    sys.exit("repomap-mcp needs the MCP SDK: pip install 'repomap[mcp]'")

from . import __version__, cli, query

INSTRUCTIONS = """\
repomap remembers which lines each test ran, who calls whom, and the argument values every function saw,
from one traced run of the test suite. Lookups read only that memory and take well under a second.

Before editing a function, call `impact` with its name to see its callers and the tests that run it.
After editing, call `impact` with no targets to get the tests your uncommitted diff reaches, run exactly
those with pytest, then call `refresh`. Use `gaps` and `values` when hardening or reviewing code: they show
inputs the suite never tried (empty, zero, negative). If `status` says there is no memory, call `record` once."""

READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

server = MCPServer("repomap", version=__version__, instructions=INSTRUCTIONS)
_cache = {"key": None, "m": None}


def memory():
    """The memory, re-read only when the file on disk changes."""
    try:
        st = cli.MEM.stat()
    except FileNotFoundError:
        raise ToolError("No repomap memory in this repo yet. Call the `record` tool once (it runs the test suite).")
    key = (st.st_mtime_ns, st.st_size)
    if _cache["key"] != key:
        _cache["m"], _cache["key"] = cli.load(), key
    return _cache["m"]


def friendly(fn):
    """The CLI exits with a message on errors; turn that into a tool error the model can read."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except SystemExit as e:
            raise ToolError(str(e.code)) from None
    return wrapper


@server.tool(annotations=READ)
@friendly
def impact(targets: list[str] | None = None, diff_rev: str | None = None, depth: int = 2) -> dict:
    """What a change reaches, and which tests to run for it.

    targets: function names (or parts of names, e.g. "Range.range_for_length") or "path/to/file.py:LINE".
    Leave targets empty to use the uncommitted changes in the working tree, compared with the tree the memory
    was recorded on (or with diff_rev, any git revision). depth: how many levels of callers to follow.

    Returns the changed functions with how many tests run each, callers by level, the selected pytest node ids,
    `untested` (changed functions no test runs, so nothing verifies them), `unmatched` (target names that match
    no function) and library files changed since the memory was recorded."""
    m = memory()
    res = cli.compute_impact(m, targets or (), diff_rev, depth=max(1, min(depth, 6)))
    res["pytest_command"] = (f"{m['python']} -m pytest -q " + " ".join(res["tests"])) if res["tests"] else None
    return res


@server.tool(annotations=READ)
@friendly
def tests_for(location: str) -> dict:
    """Which tests run a line ("path/to/file.py:LINE") or a function (a name or part of one)."""
    m = memory()
    if location.rsplit(":", 1)[-1].isdigit() and ".py:" in location:
        return {"location": location, "tests": query.run(m, "tests", location)}
    unmatched = []
    keys = cli.resolve(m, [location], unmatched)
    per = cli.tests_for(m, keys, cli.lines_index(m))
    return {"location": location, "functions": {k: sorted(v) for k, v in per.items()}, "unmatched": unmatched}


@server.tool(annotations=READ)
@friendly
def callers(function: str, depth: int = 2) -> dict:
    """Who calls a function (direct callers, then their callers, up to depth levels) and what it calls itself."""
    m = memory()
    unmatched = []
    keys = cli.resolve(m, [function], unmatched)
    callees = sorted({e for c, e in m["edges"] if c in keys and e != c})
    return {"functions": sorted(keys), "callers": [sorted(l) for l in cli.callers_of(m, keys, max(1, min(depth, 6)))],
            "calls": callees, "unmatched": unmatched}


@server.tool(annotations=READ)
@friendly
def values(function: str) -> list[dict]:
    """The argument and return values the test suite actually passed to functions whose name contains
    `function`: types seen, flags such as empty / zero / negative / None, exceptions raised, and per argument
    the probes it `never` saw."""
    return query.run(memory(), "fn", function)


@server.tool(annotations=READ)
@friendly
def gaps(module: str = "", public: bool = False, limit: int | None = None, basic: bool = False) -> list[dict]:
    """Arguments the suite never fed an edge value, for every function whose key contains `module` (empty = whole
    package): empty, zero, negative, False, and for strings non-ASCII text, a sign or 0x prefix, quotes, control
    characters, surrounding whitespace or an empty field between separators (string probes are used only if the
    memory recorded them somewhere). These are the inputs most worth a new test when hardening.

    public: drop private and nested functions. limit: keep the N most-called functions. basic: only the
    empty / zero / negative probes."""
    return query.run(memory(), "gaps", module, public=public, limit=limit, basic=basic)


@server.tool(annotations=READ)
@friendly
def status() -> dict:
    """What the memory covers (tests, functions, call edges), the snapshot it was recorded on, and which library
    files have changed since. Says so if there is no memory yet."""
    if not cli.MEM.exists():
        return {"memory": None, "hint": "No memory yet. Call `record` once (it runs the whole test suite)."}
    return cli.status(SimpleNamespace(quiet=True))


@server.tool(annotations=WRITE)
@friendly
def refresh(diff_rev: str | None = None) -> dict:
    """Bring the memory up to date after an edit: re-trace only the tests that run code in the changed files
    (plus changed test files) and merge them in. Takes seconds, not a full run."""
    memory()  # clear error if there is none
    return cli.refresh(SimpleNamespace(diff=diff_rev, by_file=False, quiet=True))


@server.tool(annotations=WRITE)
@friendly
def record(package: str | None = None, tests: str = "tests", python: str | None = None) -> dict:
    """Trace the WHOLE test suite once and save the memory (.repomap/memory.json). Slow: as long as a full test
    run plus tracing overhead (werkzeug, ~1,000 tests: 33 s). Only needed once per repo; use `refresh` after.

    package: the package directory, e.g. "src/mypkg" (guessed if there is exactly one). tests: the tests
    directory. python: interpreter with pytest and the project installed (default: ./.venv if present, else the
    one running this server)."""
    return cli.record(SimpleNamespace(package=(package or cli.guess_package()).rstrip("/"), tests=tests,
                                      python=python or cli.guess_python(), quiet=True))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.environ.get("REPOMAP_ROOT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or ".",
                    help="repo root (default: $REPOMAP_ROOT_DIR, else $CLAUDE_PROJECT_DIR, else the current directory)")
    a = ap.parse_args()
    os.chdir(Path(a.root).expanduser())
    server.run("stdio")


if __name__ == "__main__":
    main()
