# futurebench

A reward-signal environment built from the experiments in [`../experiments`](../experiments). Each episode is one Python repo frozen at the model's knowledge cutoff (2026-06-01). An agent hardens the snapshot inside a Docker sandbox with no network and nothing newer than the snapshot. A scorer outside the sandbox then runs the repo's real later tests against the agent's code and returns a reward and every part of it.

```
pip install -e rl-env/env          # needs Docker, git and uv on the host
futurebench list                   # 12 tasks: 8 train, 4 eval
futurebench reset tinydb ./ws      # builds images on first use, starts the sandbox
futurebench exec ./ws -- python -m pytest -q tests
futurebench score ./ws --out scores.json
futurebench stop ./ws
```

From Python:

```python
import futurebench as fb

ep = fb.reset("tinydb", "./ws")    # Episode: workspace, container, prompt
print(ep.prompt)                   # task prompt for the agent
code, out = ep.exec("python -m pytest -q tests")   # any harness can drive the agent through exec()
r = fb.score(ep)                   # Reward: total, terms, parts (+ detail)
ep.close()
```

Episodes are single-turn: the agent edits `/workspace` however it likes, and `score` runs once at the end. Any agent harness works as long as it runs its tool calls through `Episode.exec` (or `docker exec` into the episode container).

## Tasks

| Split | Repos | Bug units | Where they come from |
|---|---|---|---|
| train | attrs, cachetools, marshmallow, more-itertools, pluggy, sqlparse, tinydb, tomlkit | 116 | [02-scale-up](../experiments/02-scale-up/README.md), [03-analysis](../experiments/03-analysis/README.md) |
| eval | python-dotenv, cattrs, werkzeug, pydantic-settings | 30 | [04-ab-test](../experiments/04-ab-test/README.md) (held out from the prompt design) |

`src/futurebench/tasks/<repo>.json` holds the snapshot and upstream HEAD shas, package and test paths, and the target tests: the HEAD tests that fail on the snapshot code. They are grouped into **units**. A unit is the upstream commit that added the tests, or a single test where no commit was found, and each unit carries its bug, feature or unclear label. `python -m futurebench.build_tasks rl-env/experiments` regenerates the manifests from the experiment files, and a unit test checks that they still match.

The eval split's improved prompt was written from the train split's misses. python-dotenv is the least blind of the eval repos.

## Reward

```
R = 1.0  × bugs_fixed / bugs_available
  + 0.25 × min(verified_tests / 20, 1)
  − 1.0  × regressions / bugs_available
  − 0.1  × collisions per 100 changed lines
```

| Part | How it's measured |
|---|---|
| `bugs_fixed` | Bug units whose target tests all pass on upstream HEAD's suite with the agent's package code swapped in. `--bug-credit any` or `fraction` changes the rule. Feature and unclear units are reported in `detail.units` but never rewarded. |
| `verified_tests` | Distinct test functions the agent added that pass on its code and fail on the snapshot code (run: agent tests on snapshot code). |
| `regressions` | Distinct test functions that passed before the patch and fail after it: the snapshot's own tests run on the agent's code (with the snapshot's test files, so editing a test can't hide a regression), plus HEAD tests that passed on the snapshot code. |
| `collisions` | Upstream first-parent commits after the snapshot that add textual merge conflicts against the agent's patch (`git merge-tree`, ported from [05-replay](../experiments/05-replay/README.md)). |

Each scoring writes the parts as well as the total. `futurebench reweigh scores.json --collisions 0.05` therefore recomputes the totals without running a single test. All weights are flags on `score` and `rescore`.

## Isolation

- **agent image** (`futurebench/<task>:agent-…`): the snapshot tree, its dependencies in `/opt/venv`, and git. The episode container runs with `--network none` and the host workspace mounted at `/workspace`.
- **score image** (`futurebench/<task>:score-…`): upstream HEAD's tree and its dependencies at `/future`. Only the scorer uses it, in throwaway containers on copies of the workspace. Nothing newer than the snapshot is ever in an episode container.
- Images are built with `docker run` + `docker commit` on `mirror.gcr.io/library/python:3.13`, which avoids Docker Hub's anonymous rate limit. The host's `uv`, proxy variables and CA bundle are lent to the build and not baked into the image. Set `FUTUREBENCH_BASE_IMAGE` to use another base.
- Install-generated files inside package code, such as a hatch-vcs `_version.py`, are kept in `/opt/generated` and copied into any mounted tree that lacks them.

This replaces the move-the-data-and-audit-the-transcript isolation of 02 to 04. `../harness/audit.py` still works on transcripts as a second check.

## Validation

`futurebench rescore TASK PATCH...` applies an old run's patch to a fresh workspace and scores it. All 48 patches from 02-scale-up (24), its sandboxed reruns (8) and 04-ab-test (16) were re-scored. [`validation/README.md`](validation/README.md) has the comparison with the old harness's scores.

## Layout

```
src/futurebench/
  manifest.py     Task, list_tasks(), get_task()
  env.py          reset(), score(), Episode
  reward.py       Weights, Parts, Reward, bug credit
  sandbox.py      image builds, throwaway and episode containers
  replay.py       upstream-history collisions
  build_tasks.py  manifests from the experiment files
  tasks/          12 frozen task manifests
  prompts/        improved (default) and baseline prompts from 04-ab-test
tests/            unit tests (no Docker needed)
validation/       rescore results for the 48 old patches
```

Cache: `FUTUREBENCH_HOME` (default `~/.cache/futurebench`) holds the clones and the per-task baseline test results.
