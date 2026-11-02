# repomap MCP server

**Status: planned.**

Expose the repomap memory as MCP tools so any agent (Claude Code, IDE assistants, the RL environment's agents) gets change impact without shelling out and parsing text.

## Tools

| Tool | Wraps | Returns |
|---|---|---|
| `impact(targets?: string[])` | `repomap.py impact` | changed functions, callers by level, selected test ids |
| `tests_for(location)` | `query.py tests FILE:LINE` | tests that run a line |
| `callers(function)` | `query.py callers` | call graph neighbours |
| `values(function)` | `query.py fn` | argument and return value summary |
| `gaps(module?)` | `query.py gaps` | arguments never seen empty / zero / negative / None |
| `refresh()` | `repomap.py refresh` | which tests were re-traced |
| `status()` | `repomap.py status` | memory commit and drift |

## Plan

1. Add `--json` to `impact` and `query.py` in the CLI (shared with the other front ends).
2. A small stdio server (Python, `mcp` SDK) that loads `.repomap/memory.json` once and answers from memory, re-reading when the file changes, as `serve` already does.
3. Ship with the [AI skill](../ai-skill/README.md) as one plugin.
4. Use it in the RL environment as the agent's tool interface, so the env and real users exercise the same code.
