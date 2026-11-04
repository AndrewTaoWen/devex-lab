# repomap for Claude Code: skill + MCP server plugin

**Status: v0.1.** This folder is a Claude Code plugin. It bundles:

- [`skills/repomap/SKILL.md`](skills/repomap/SKILL.md): tells the agent to check impact before editing a function, run only the selected tests after, refresh the memory, and look at value gaps when hardening.
- [`.mcp.json`](.mcp.json): starts `repomap-mcp`, the [MCP server](../mcp-server/README.md), so the skill gets JSON tools instead of parsing CLI text. Without the server, the skill falls back to the `repomap` CLI.

## Install

The plugin starts the `repomap-mcp` command, so install the package first, into any environment on your `PATH` (it doesn't need to be the project's environment):

```
pip install "repomap[mcp] @ git+https://github.com/AndrewTaoWen/devex-lab#subdirectory=devtools/cli"
# once it is on PyPI:  pip install "repomap[mcp]"   or   pipx install "repomap[mcp]"
```

Then, in Claude Code:

```
/plugin marketplace add AndrewTaoWen/devex-lab
/plugin install repomap@devex-lab
```

For local development, `claude --plugin-dir devtools/ai-skill` loads it straight from this checkout.

The first time in a repo, the agent calls `record` (one traced run of the suite). Tests run with the repo's `.venv` interpreter if it has one, otherwise pass `python` to `record`.

## Why

Agents edit code without knowing who depends on it, then either run the whole suite (slow) or nothing (risky). repomap answers both questions in under a second: what a change reaches, and which tests to run. Its value summaries also tell an agent what inputs a function has actually seen, which the miss analysis in [`rl-env/experiments/03-analysis/`](../../rl-env/experiments/03-analysis/README.md) says is where agents miss bugs (empty inputs, domain rules).

## Checked so far

- `claude plugin validate` passes for the plugin and the repo's marketplace file.
- With the plugin loaded in headless Claude Code on the shopkit example, the MCP server connects and the agent called `mcp__plugin_repomap_repomap__impact` on `round_cents`, which selected the right 6 tests.

## To do

- Measure it: the A/B in [ROADMAP.md](../../ROADMAP.md) item 2 uses this skill as the treatment arm.
- Publish `repomap` to PyPI so the install is one line.
