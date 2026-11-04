# repomap MCP server

**Status: v0.1.** `repomap-mcp` exposes the repomap memory as MCP tools, so any agent (Claude Code, IDE assistants, the reward benchmark's agents) gets change impact without shelling out and parsing text. The code is [`cli/src/repomap/mcp_server.py`](../cli/src/repomap/mcp_server.py), part of the `repomap` package.

```
pip install "./devtools/cli[mcp]"        # from this repo; PyPI: pip install "repomap[mcp]"
repomap-mcp [--root DIR]                 # stdio; root defaults to $REPOMAP_ROOT_DIR, $CLAUDE_PROJECT_DIR, then cwd
```

To add it to Claude Code without the plugin: `claude mcp add repomap -- repomap-mcp`. The [Claude Code plugin](../ai-skill/README.md) bundles it with the skill.

## Tools

| Tool | Read-only | Returns |
|---|---|---|
| `impact(targets?, diff_rev?, depth=2)` | yes | changed functions, callers by level, selected pytest node ids and a ready `pytest_command`, `untested`, `unmatched`. No targets = your uncommitted diff |
| `tests_for(location)` | yes | tests that run `file.py:LINE`, or each function matching a name |
| `callers(function, depth=2)` | yes | callers by level, and what the function calls |
| `values(function)` | yes | argument and return value summaries, with the probes each argument `never` saw |
| `gaps(module?)` | yes | arguments never seen empty / zero / negative |
| `status()` | yes | memory size, snapshot commit, library files changed since |
| `refresh(diff_rev?)` | no | re-traces only tests touching changed files; seconds |
| `record(package?, tests="tests", python?)` | no | traces the whole suite once; slow, and says so in its description |

The read-only tools carry MCP's `readOnlyHint`, so clients can allow them without prompting. The server keeps the memory loaded and re-reads `.repomap/memory.json` when it changes, so a `refresh` from the CLI shows up on the next call. CLI failures (no memory yet, a tracing error) come back as tool errors with the message, not crashes.

There is deliberately no `run_tests` tool: agents already have a shell, and `impact` hands them the command.

## Tests

[`cli/tests/test_mcp_server.py`](../cli/tests/test_mcp_server.py) starts the server over real stdio with the MCP client SDK on a recorded copy of the shopkit example: it lists the tools and their hints, runs an edit, `impact`, `refresh`, `impact` cycle, checks every lookup, and checks the error when there is no memory.

## Next

- Use it as the tool interface for agents in the reward benchmark, so the benchmark and real users exercise the same code.
- A versioned schema for the tool results (shared with the CLI's `--json`).
