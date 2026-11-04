# Repo lifetime: developer experience for code that has to last

This project asks one question from two sides: **what happens to code after it is written?**

1. **[Reward benchmark](rl-env/README.md)** (`rl-env/`). Score models on whether their code survives a repository's real future. Each repo is frozen at the model's knowledge cutoff (2026-06-01), an agent hardens the snapshot blind, and the repo's own later tests become an automatic reward.
2. **[Dev tools](devtools/README.md)** (`devtools/`). Tools that tell a developer (or an agent) what a change will affect before anything runs. They are built on **repomap**, which traces a test suite once and remembers which lines each test runs, who calls whom, and what values every function saw.

The two feed each other. The benchmark needs fast, precise "what does this change touch" answers to score and to guide agents, and the dev tools get a benchmark built from real repo history.

## What I'm working on now

- **Turning repomap into dev tools.** The CLI is now an installable package (`repomap` and `repomap-mcp` commands), with an MCP server and a Claude Code plugin that bundles the skill ([`devtools/ai-skill/`](devtools/ai-skill/README.md)). Next: publish to PyPI, then the GitHub Action.
- **Turning the pilot into a reward benchmark.** First version built: [futurebench](rl-env/env/README.md) has `prepare` / `score`, a Docker sandbox per attempt with no network, the combined reward (future bug tests + verified regression tests − regressions − replay collisions), and 12 tasks. Re-scoring every old patch reproduces the earlier results. Next: tune the reward weights, add a reference agent loop, and grow the task set.
- **Connecting the two.** An A/B test that gives hardening agents repomap's value gaps ("this argument was never empty") as hints.

The full priority list is in [ROADMAP.md](ROADMAP.md).

## Layout

```
rl-env/
  env/              futurebench: the reward benchmark (prepare / score, Docker sandbox, 12 tasks)
  harness/          screen.py, blind.py, label.py, audit.py, replay.py, future_tests.sh
  experiments/      01-pilot, 02-scale-up, 03-analysis, 04-ab-test, 05-replay (in the order they ran)
  research/         survey of repo-evolution benchmarks and training data
devtools/
  cli/              the repomap package (src/repomap: CLI, MCP server, tracer, map page), plus mutbench.py and impact.py
  ai-skill/         Claude Code plugin: the repomap skill + MCP server config
  mcp-server/       docs for repomap-mcp, the MCP tools for any agent
  vscode-extension/ impact and coverage in the editor                           (planned)
  github-action/    test selection and an impact comment on every PR            (planned)
tests/              tests for the harness's shell and XML hardening
```

## Headline findings

1. **Future tests make a usable reward signal.** Running a repo's later test suite against its past code gives an automatic score for "future-proof" code. Precision is high: 84% of the agents' fixes are real bugs, and 56% of all fixes are real bugs that upstream still hasn't fixed. So the future-test score is a floor.
2. **Agents anticipate latent bugs, not features.** Across 8 repos with 3 blind runs each, agents fixed 26% of the failing future tests with 3 regressions in 24 runs. They fixed about a third of the bug tests and none of the 49 feature tests.
3. **Misses come in whole kinds of bugs.** Agents are strong on state and caching bugs (77% caught) and weak on output formatting, domain rules, empty inputs and performance. A prompt that tells them to search those kinds raised distinct future bugs caught from 15 to 21 of 60 on held-out repos, with 0 regressions.
4. **Hardening barely gets in the way of real history.** Replaying upstream's later commits onto agent-hardened code, 15% of run-commit pairs collide, about the same for both prompts.
5. **A traced map predicts change impact almost perfectly.** On werkzeug, choosing tests by changed function caught all 85 tests whose result changed across 54 real commits, while running 24% of the suite. Against 200 injected bugs, every bug was caught and 99.7% of broken tests were selected, at a median of 0.6% of the suite.

## Quick start

```
# dev tools: install, trace a repo once, then ask what a change affects
pip install "./devtools/cli[mcp]"   # or run python devtools/cli/repomap.py without installing
repomap record --package src/mypkg
repomap impact                      # your uncommitted diff
repomap serve                       # map and graph at http://localhost:8765

# Claude Code: the skill + MCP server as a plugin
/plugin marketplace add AndrewTaoWen/devex-lab
/plugin install repomap@devex-lab

# reward benchmark: run and score one attempt (needs Docker)
pip install -e rl-env/env
futurebench prepare tinydb ./ws && futurebench score ./ws

# the original scripts: find a repo's failing future tests, then run and score blind agents
python rl-env/harness/screen.py --help
python rl-env/harness/blind.py --help
```

Live pages (private until shared): [Werkzeug Repo Map](https://claude.ai/artifact/RVd9ak5h1iP4Zhkck5CoPc), [Werkzeug Change Impact](https://claude.ai/artifact/9AEvdA1qQ2EcVkbdqfVmjq), [Shopkit Impact Examples](https://claude.ai/artifact/5QMS2QWpJhtr9LEPttiyeG).

The harness runs tests and setup code from cloned repos by design; see [SECURITY-SETUP.md](SECURITY-SETUP.md).
