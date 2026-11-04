# devex-lab: can an AI fix bugs before anyone finds them?

## The big question

Software keeps changing after it ships. People find bugs, report them, and someone fixes them, often months later. This project asks: **can an AI look at code today and fix the problems people will only discover later?**

## How we test it

We can't see the future, but we can look at the past.

1. Take a real open-source project and roll it back to how it looked on a fixed date (2026-06-01, the AI model's knowledge cutoff, so the AI can't have seen anything that came after).
2. Give the AI that old code and ask it to make the code better. It gets no hints about what went wrong later.
3. Check its work against the tests the real project added *after* that date. Those tests were written because people found problems. If the AI's version already passes them, the AI fixed real bugs ahead of time.

This gives an automatic score with no human grading: the project's own future is the answer key.

## What we learned

- **The AI fixed about a third of the future bugs.** Across 8 projects with 3 tries each, it fixed 26% of the future tests that failed on the old code, and about a third of the ones that were about bugs. Across all 24 tries, it broke something that used to work only 3 times.
- **It fixed none of the future new features.** That makes sense: nobody can guess what a project will decide to add.
- **Most of its changes were real improvements.** We checked 134 of its fixes by hand: 84% fixed a real bug, and 56% of all its fixes are bugs the real projects *still haven't fixed*. So the score undercounts how much it helps.
- **It misses whole kinds of bugs, not random ones.** It's good at bugs about saved state and caching, and weak at output formatting, project-specific rules, empty inputs and speed.
- **A better prompt helped.** Telling it to look for the kinds of bugs it usually misses raised the number of distinct future bugs it caught from 15 to 21 (out of 60) on projects it had never been tested on, without breaking anything.
- **Its changes rarely get in the way.** When we replayed the real project's later changes on top of the AI's version, only 15% of the pairs clashed.

## What we built

### futurebench: a reusable scoring tool

We turned the whole check into a **reward benchmark** called [futurebench](reward-bench/futurebench/README.md), so any AI can be graded the same way. You ask it to prepare a task, the AI works on the old code inside a locked-down Docker container with no internet, and then futurebench scores the result. It has 12 projects so far. Scoring all 48 of our earlier attempts with it gives exactly the same results as before, which tells us it measures the same thing.

### repomap: run only the tests that matter

Along the way we built **repomap**. It watches your test suite run once and remembers which tests touch which lines of code. After that, when you change something, it tells you which tests actually matter, so you run a small slice of your tests instead of all of them.

- On werkzeug (a popular Python web library), it picked out every one of the 85 tests affected by 54 real changes while running only 24% of the tests.
- When we planted 200 small bugs on purpose, it caught every one that any test could catch (171 of 171), usually by running just 6 of the 1,003 tests (about 0.6%).

It installs with `pip`, and Claude (and other AI tools) can use it directly through its MCP server and Claude Code plugin.

## What I'm working on now

- **Finishing futurebench.** It's built, with clearer command names and its own home in `reward-bench/`. Next: decide how to weigh the parts of the score, add a simple reference AI that runs the tasks end to end, and add more projects.
- **Shipping repomap as a package.** It installs with `pip` (`repomap` and `repomap-mcp` commands) and comes with an MCP server and a Claude Code plugin ([`devtools/ai-skill/`](devtools/ai-skill/README.md)). Next: pick a license, publish it to PyPI, then build the GitHub Action.
- **Done, and it didn't pan out:** feeding repomap's hints ("this input was never empty in any test") to the AI. A quick check showed those hints almost never point at the bugs people found later, so we stopped before running the AI ([write-up](reward-bench/experiments/06-gaps-ab/README.md)).

The full priority list is in [ROADMAP.md](ROADMAP.md).

## Words you'll see

- **Snapshot:** a project rolled back to how it looked on 2026-06-01.
- **Future tests:** tests the real project added after that date.
- **Hardening:** asking the AI to improve the snapshot without telling it what goes wrong later.
- **Regression:** something that worked before and broke after the AI's change.
- **Reward benchmark:** a fixed set of tasks plus an automatic score, so different AIs can be compared fairly.

## Where things are

```
reward-bench/
  futurebench/      the scoring tool (prepare / score, Docker sandbox, 12 projects)
  harness/          the original scripts: screen.py, blind.py, label.py, audit.py, replay.py, future_tests.sh
  experiments/      01-pilot, 02-scale-up, 03-analysis, 04-ab-test, 05-replay (in the order they ran)
  research/         survey of related benchmarks and training data
devtools/
  cli/              the repomap package (record | impact | refresh | status | serve, MCP server, map page), plus mutbench.py and impact.py
  ai-skill/         Claude Code plugin: the repomap skill + MCP server config
  mcp-server/       docs for repomap-mcp, the tools any AI agent can call
  vscode-extension/ impact and coverage in the editor                           (planned)
  github-action/    run only the affected tests on every PR                      (planned)
tests/              tests for the harness's safety checks
```

## Try it

```
# repomap: install, watch your tests once, then ask what a change affects
pip install "./devtools/cli[mcp]"   # or run python devtools/cli/repomap.py without installing
repomap record --package src/mypkg
repomap impact                      # your uncommitted changes
repomap serve                       # a map and graph at http://localhost:8765

# Claude Code: the repomap skill + MCP server as a plugin
/plugin marketplace add AndrewTaoWen/devex-lab
/plugin install repomap@devex-lab

# futurebench: prepare one task, let an AI work on ./ws, then score it (needs Docker)
pip install -e reward-bench/futurebench
futurebench prepare tinydb ./ws && futurebench score ./ws

# the original scripts: find a project's failing future tests, then run and score AI attempts
python reward-bench/harness/screen.py --help
python reward-bench/harness/blind.py --help
```

Live pages (private until shared): [Werkzeug Repo Map](https://claude.ai/artifact/RVd9ak5h1iP4Zhkck5CoPc), [Werkzeug Change Impact](https://claude.ai/artifact/9AEvdA1qQ2EcVkbdqfVmjq), [Shopkit Impact Examples](https://claude.ai/artifact/5QMS2QWpJhtr9LEPttiyeG).

These scripts run tests and setup code from the projects they download, on purpose. Read [SECURITY-SETUP.md](SECURITY-SETUP.md) before running them.
