# Repo lifetime simulation: an RL environment

Can a model write code that survives a repository's future? Each repo is frozen at the model's knowledge cutoff (2026-06-01). An agent hardens that snapshot without seeing anything later. The repo's own later tests, run against the hardened code, then act as an automatic score: no LLM judge and no synthetic data.

## How an episode works today

1. **Screen** (`harness/screen.py`). Clone a repo, take the last commit before the cutoff and the current HEAD, run HEAD's tests against both. Tests that pass on HEAD and fail on the snapshot are the target.
2. **Label** (`harness/label.py`). Find the commit that added each target test so it can be marked bug, feature or unclear. Only bug tests are a fair target; features can't be anticipated from code.
3. **Harden blind** (`harness/blind.py prep`). Give a fresh agent a bare copy of the snapshot with no history and no future data.
4. **Audit** (`harness/audit.py`). Check the agent's transcript for network use or reads of future data.
5. **Score** (`harness/blind.py score`). Swap the agent's package code into HEAD's tree and run HEAD's tests: fixed target tests, regressions.
6. **Replay** (`harness/replay.py`). Merge upstream's real later commits onto the agent's patch and count collisions, as a maintainability cost.

`harness/future_tests.sh` is the one-repo version of steps 1 and 5, used in the first pilot.

## Experiments

Run in this order, each building on the last:

| Step | What it asks | Result |
|---|---|---|
| [01-pilot](experiments/01-pilot/README.md) | Can an agent anticipate tinydb's and click's later fixes? | tinydb future failures 9 → 5, 4 of 8 real bugs caught |
| [02-scale-up](experiments/02-scale-up/README.md) | Same on 8 repos, 3 blind runs each, plus labels and sandboxed reruns | 26% of target tests fixed, 32% of bug tests, 0 of 49 feature tests |
| [03-analysis](experiments/03-analysis/README.md) | Which bugs are missed, and how many fixes are real? | Misses are whole kinds of bug; 84% of fixes real, 56% still unfixed upstream |
| [04-ab-test](experiments/04-ab-test/README.md) | Does a prompt built from the misses catch more on held-out repos? | Distinct future bugs 15 → 21 of 60, 0 regressions |
| [05-replay](experiments/05-replay/README.md) | Do agent patches get in the way of real later commits? | 15% of run-commit pairs collide, similar for both prompts |
| [06-gaps-ab](experiments/06-gaps-ab/README.md) | Do repomap's untested-value gaps help hardening agents? | In progress: basic gaps name 1 of 10 werkzeug future bugs; richer probes added |

[`research/repo-evolution-training-data.md`](research/repo-evolution-training-data.md) surveys related benchmarks (SWE-CI, SWE-Milestone, SWE-Chain, SWE-EVO) and training data as of October 2026.

## What turns this into an environment

Planned as `rl-env/env/` (see [ROADMAP.md](../ROADMAP.md), item 1):

- **API.** `reset(repo, snapshot)` returns a workspace; `score(workspace)` returns the reward and its parts.
- **Reward.** Future bug tests fixed + verified regression tests that fail on the snapshot − regressions − upstream replay conflicts. The future-test part is a floor, since most real fixes are bugs upstream hasn't reported yet.
- **Isolation.** One container per episode with future data outside it, replacing "move the data away and audit the transcript".
- **Data.** The 12 repos run so far, then the rest of the screened repos, larger repos with feature tests filtered to snapshot APIs, then other languages.
- **Change impact.** [repomap](../devtools/cli/README.md) can pick the tests to run per episode (cheaper scoring) and hand the agent value gaps as hints.
