# Dev tools

One engine, several front ends. The engine is **repomap** ([`cli/`](cli/README.md)): it traces a test suite once under Python's `sys.monitoring` and remembers which lines each test runs, the call graph, and a summary of the values every function saw. Everything else here is a way to put that memory where a developer or an agent already works.

| Sub-project | Status | What it does |
|---|---|---|
| [`cli/`](cli/README.md) | Working prototype | `repomap.py record \| impact \| refresh \| status \| serve`, plus `mutbench.py` (fault-injection benchmark) and `impact.py` (scoring against real history) |
| [`ai-skill/`](ai-skill/README.md) | Draft | A skill so Claude Code checks impact before an edit and runs only the affected tests after |
| [`mcp-server/`](mcp-server/README.md) | Planned | The same lookups as MCP tools for any agent or IDE |
| [`github-action/`](github-action/README.md) | Planned | Test selection in CI and an impact comment on every PR |
| [`vscode-extension/`](vscode-extension/README.md) | Planned | "N tests run this line" in the gutter, value hovers, graph view of the current diff |

## Shared contract

All front ends call the CLI rather than reimplementing it, so the first step for each is the same: package the CLI (`pip install repomap`, `pytest --repomap`) and give `impact` and `query` a `--json` output with a versioned schema. The memory lives in `.repomap/memory.json` and is tied to a commit via `refs/repomap/memory`.

## Numbers to keep in mind

- Lookups take 0.2 to 0.8 s; `refresh` takes 2 to 16 s against 33 s for a full record on werkzeug.
- Changed-function selection caught 85 of 85 result-changing tests across 54 real werkzeug commits at 24% of the suite.
- On 200 injected faults: 171 of 171 caught, 99.7% of broken tests selected, median 0.6% of the suite.

The roadmap for all of these is in [ROADMAP.md](../ROADMAP.md).
