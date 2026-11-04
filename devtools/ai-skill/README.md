# repomap AI skill

**Status: draft.** [`SKILL.md`](SKILL.md) is a working first version for Claude Code. It assumes `repomap.py` is on the path (or at `devtools/cli/repomap.py` in this repo) and that the repo has been recorded once.

## Why

Agents edit code without knowing who depends on it, then either run the whole suite (slow) or nothing (risky). repomap answers both questions in under a second: what a change reaches, and which tests to run. Its value summaries also tell an agent what inputs a function has actually seen, which the miss analysis in [`reward-bench/experiments/03-analysis/`](../../reward-bench/experiments/03-analysis/README.md) says is where agents miss bugs (empty inputs, domain rules).

## What the skill does

1. If `.repomap/memory.json` is missing, record once.
2. Before editing a function, run `impact <function>` and read the callers and test count.
3. After editing, run `impact` on the diff, run only the selected tests, then `refresh`.
4. When hardening or reviewing, query the value gaps for the functions touched.

## To do

- Switch to `--json` output once the CLI has it, so the skill doesn't parse text.
- Measure it: the A/B in [ROADMAP.md](../../ROADMAP.md) item 2 uses this skill as the treatment arm.
- Publish as a plugin alongside the MCP server.
