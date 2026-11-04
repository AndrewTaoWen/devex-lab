# Roadmap

Two directions, one shared core. Items are in priority order within each horizon. "Done" points at the write-up.

## Done so far

| Direction | What | Where |
|---|---|---|
| Reward bench | Pilot on tinydb and click: future tests work as a reward | [01-pilot](rl-env/experiments/01-pilot/README.md) |
| Reward bench | 8 repos x 3 blind runs, bug vs feature labels, sandboxed reruns | [02-scale-up](rl-env/experiments/02-scale-up/README.md) |
| Reward bench | Miss analysis and precision of agent fixes | [03-analysis](rl-env/experiments/03-analysis/README.md) |
| Reward bench | Prompt A/B on 4 held-out repos | [04-ab-test](rl-env/experiments/04-ab-test/README.md) |
| Reward bench | Replay of upstream history onto agent patches | [05-replay](rl-env/experiments/05-replay/README.md) |
| Reward bench | futurebench: reset/score benchmark with Docker sandboxes, validated on 48 old patches | [env](rl-env/env/README.md) |
| Dev tools | repomap CLI: record, impact, refresh, status, serve (map + graph view) | [cli](devtools/cli/README.md) |
| Dev tools | Accuracy on werkzeug history (85/85) and 200 injected faults (171/171) | [cli](devtools/cli/README.md) |

## Next (weeks)

1. **Reward benchmark.** Built as [futurebench](rl-env/env/README.md): `reset(task)` → sandboxed attempt, `score(attempt)` → reward and parts. Reward = future bug units fixed + verified snapshot-failing regression tests − regressions − upstream replay collisions. Docker per episode with no network, and future tests only in a separate score image. 12 tasks (8 train, 4 eval). Re-scoring all 48 old patches reproduces the old harness's results. Next: a reference agent loop, calibrating the collision weight, and more tasks once screen.py handles the setup failures.
2. **repomap inside the hardening agent.** Give agents `query.py`'s value gaps ("this argument was never empty") and A/B them against the improved prompt on the held-out repos. It is the cheapest test of whether the two directions help each other, and the miss analysis says empty inputs and domain rules are exactly what agents skip.
3. **AI skill and MCP server.** Wrap the CLI so Claude Code (and any MCP client) runs `impact` before editing and selected tests after. A few days of work, usable immediately, and it gives the benchmark's agents the same tool interface. ([ai-skill](devtools/ai-skill/README.md), [mcp-server](devtools/mcp-server/README.md))
4. **Package the CLI.** `pip install repomap` with a pytest plugin entry point (`pytest --repomap`), a stable JSON schema for `.repomap/memory.json`, and versioned output. Every other dev tool depends on this.

## Then (1 to 2 months)

5. **GitHub Action.** On each PR: refresh the map from a cached base, run only selected tests, and post the impact graph as a comment. Fall back to the full suite when the map is stale. ([github-action](devtools/github-action/README.md))
6. **VS Code extension.** Gutter marks for "N tests run this line", hover with value summaries, a side panel with the graph view for the current diff, and "run affected tests". ([vscode-extension](devtools/vscode-extension/README.md))
7. **More data for the env.** More runs per repo for variance, the screened repos not yet run (boltons, dateutil, humanize, packaging and others in `02-scale-up/screen/`), larger repos (click-sized) with feature tests filtered to APIs that existed at the snapshot.
8. **Per-commit change graphs.** Record the graph view for every commit in a repo's history. That gives a timeline of how change impact spreads, and it is training data in its own right: predict the graph from the diff.

## Later (bigger bets)

9. **Other languages.** A JS/TS tracer (V8 coverage + call edges) and Go, so both the env and the tools leave Python. Picks repos where the model's cutoff leaves months of history.
10. **Public benchmark.** Publish mutbench faults and real-history impact for all pilot repos so any test-impact tool (or model) can be scored on them.
11. **Ecosystem ideas worth a stub when someone picks them up:**
    - **Future-proof review bot.** Run the improved hardening prompt against a PR's changed functions, with repomap's gaps, and comment the likely latent bugs. The benchmark's hardening agent as a reviewer.
    - **Test-pollution detector.** mutbench's last misses were order-dependent tests. repomap already sees shared state, so it can flag tests that pass only after others.
    - **Agent memory service.** Serve value summaries and call edges as long-lived memory for coding agents across sessions, refreshed incrementally.
    - **Dead and untested code report.** Functions no test reaches, ranked by how many callers they have.

## Open questions

- Does a reward that credits unreported fixes (56% of agent fixes are real but unfixed upstream) need a human or model reviewer in the loop, or is "fails on snapshot, passes after" enough?
- How stale can a map get before the Action should re-record in full? The werkzeug snapshot map kept 89% recall across 54 commits.
