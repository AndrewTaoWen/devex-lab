# Validation: re-scoring the old patches

Every patch from the earlier experiments was applied to a fresh futurebench workspace and scored in Docker with `futurebench rescore`: 24 from 02-scale-up, 8 sandboxed reruns, and 16 from 04-ab-test. `compare.py` puts the results next to the old harness's `scores.json`.

```
python compare.py . ../../experiments
```

## Result

**48 of 48 patches fix exactly the same set of future tests as before, with the same number of future-test regressions.** The new scorer runs in Docker images built from scratch and splits code and tests differently from `blind.py score`, so this is an independent check of both.

| Set | Runs | Bug units, strict ("all" tests pass) | Bug units, "any" test passes | Mean R | Mean terms: bugs / tests / regressions / collisions |
|---|---|---|---|---|---|
| 02-scale-up | 24 | 83 of 348 | 92 | 0.303 | 0.241 / 0.204 / −0.007 / −0.135 |
| 02 sandboxed | 8 | 28 of 116 | 32 | 0.361 | 0.338 / 0.197 / −0.016 / −0.159 |
| 04-ab-test | 16 | 24 of 120 | 36 | 0.199 | 0.196 / 0.188 / 0.000 / −0.184 |

On 04-ab-test, "any" credit reproduces the old count of distinct bugs caught: 15 for the baseline prompt and 21 for the improved one, out of 60 run-bug pairs per arm. Strict credit gives 8 and 16. The improved prompt also wins on total reward: mean R 0.125 for the baseline and 0.272 for the improved prompt (0.315 and 0.451 without the collision term).

## What this says about the default weights

- **The collision term is too big.** At 0.1 per collision per 100 changed lines, it averages −0.18 on the eval split, about the same size as the bug term. It outweighs the bug term in 18 of 48 runs. It also makes R negative for every pydantic-settings run and for python-dotenv r1 and r2. And because the term is a rate, a small patch with one collision is punished more than a large one. A weight around 0.02, or a cap on the term, would keep it a tiebreaker. `futurebench reweigh FILE --collisions 0.02` shows the effect without rerunning anything.
- **The test term is nearly saturated.** Most runs add 15 or more verified test functions, so the term sits at 0.19 to 0.20 of its 0.25 cap and barely separates runs. A cap of 50 would spread it out.
- **Strict credit is harsher than the earlier write-ups.** Many bug units have several parametrized tests, and agents often fix only some of them. Strict credit is the honest number for "fixed the bug". `--bug-credit fraction` is a smoother alternative.

## Notes

- Verified test counts can differ by 1 to 3 between identical reruns. cachetools-r2 gave 10, then 11, and cattrs-r1 gave 14, then 17. A few agent-written tests are timing- or order-dependent. Fixed future tests and regressions never changed between reruns.
- werkzeug runs with `-m "not dev_server"`. With its optional watchdog extra installed, the reloader test never returns in the sandbox, and no target test is a dev-server test.
- cattrs r3 and r4 were scored on the `python:3.13-slim` base. Every other run used the full `python:3.13` base, the default because it includes git. Fixed tests were identical on both bases for every run scored on both.

## Per run

| Set | Run | Old fixed tests | New fixed tests | Same set | Old future regressions | New | Old bug units (any) | New bug units (any) | New bugs (all) | Verified tests | Regressions (functions) | Collisions | R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 02-scale-up | attrs-r1 | 0 | 0 | yes | 0 | 0 |  | 0 | 0/2 | 16 | 0 | 0 | 0.200 |
| 02-scale-up | attrs-r2 | 0 | 0 | yes | 0 | 0 |  | 0 | 0/2 | 5 | 0 | 0 | 0.062 |
| 02-scale-up | attrs-r3 | 0 | 0 | yes | 0 | 0 |  | 0 | 0/2 | 20 | 0 | 1 | 0.232 |
| 02-scale-up | cachetools-r1 | 8 | 8 | yes | 0 | 0 |  | 2 | 2/8 | 20 | 0 | 4 | 0.389 |
| 02-scale-up | cachetools-r2 | 7 | 7 | yes | 0 | 0 |  | 2 | 2/8 | 11 | 0 | 5 | 0.161 |
| 02-scale-up | cachetools-r3 | 12 | 12 | yes | 1 | 1 |  | 4 | 4/8 | 14 | 1 | 4 | 0.396 |
| 02-scale-up | marshmallow-r1 | 1 | 1 | yes | 0 | 0 |  | 1 | 1/6 | 23 | 0 | 1 | 0.390 |
| 02-scale-up | marshmallow-r2 | 1 | 1 | yes | 0 | 0 |  | 1 | 1/6 | 21 | 0 | 1 | 0.393 |
| 02-scale-up | marshmallow-r3 | 1 | 1 | yes | 0 | 0 |  | 1 | 1/6 | 21 | 0 | 1 | 0.396 |
| 02-scale-up | more-itertools-r1 | 11 | 11 | yes | 0 | 0 |  | 6 | 5/18 | 21 | 0 | 8 | 0.356 |
| 02-scale-up | more-itertools-r2 | 11 | 11 | yes | 0 | 0 |  | 6 | 5/18 | 13 | 0 | 8 | 0.144 |
| 02-scale-up | more-itertools-r3 | 11 | 11 | yes | 0 | 0 |  | 6 | 5/18 | 9 | 0 | 7 | 0.016 |
| 02-scale-up | pluggy-r1 | 7 | 7 | yes | 0 | 0 |  | 5 | 4/14 | 12 | 0 | 10 | 0.206 |
| 02-scale-up | pluggy-r2 | 9 | 9 | yes | 0 | 0 |  | 6 | 5/14 | 9 | 0 | 6 | 0.291 |
| 02-scale-up | pluggy-r3 | 6 | 6 | yes | 0 | 0 |  | 4 | 3/14 | 11 | 0 | 6 | 0.199 |
| 02-scale-up | sqlparse-r1 | 3 | 3 | yes | 0 | 0 |  | 2 | 2/5 | 23 | 0 | 4 | 0.559 |
| 02-scale-up | sqlparse-r2 | 3 | 3 | yes | 0 | 0 |  | 2 | 2/5 | 19 | 0 | 1 | 0.613 |
| 02-scale-up | sqlparse-r3 | 2 | 2 | yes | 0 | 0 |  | 1 | 1/5 | 17 | 0 | 2 | 0.366 |
| 02-scale-up | tinydb-r1 | 4 | 4 | yes | 0 | 0 |  | 3 | 3/8 | 27 | 0 | 6 | 0.509 |
| 02-scale-up | tinydb-r2 | 3 | 3 | yes | 0 | 0 |  | 2 | 2/8 | 23 | 0 | 5 | 0.372 |
| 02-scale-up | tinydb-r3 | 4 | 4 | yes | 0 | 0 |  | 3 | 3/8 | 23 | 0 | 6 | 0.478 |
| 02-scale-up | tomlkit-r1 | 56 | 56 | yes | 0 | 0 |  | 12 | 11/55 | 25 | 0 | 11 | 0.260 |
| 02-scale-up | tomlkit-r2 | 52 | 52 | yes | 2 | 2 |  | 12 | 11/55 | 18 | 2 | 11 | 0.109 |
| 02-scale-up | tomlkit-r3 | 55 | 55 | yes | 0 | 0 |  | 11 | 10/55 | 18 | 0 | 8 | 0.186 |
| 02 sandboxed | attrs-r1 | 1 | 1 | yes | 0 | 0 |  | 1 | 1/2 | 16 | 0 | 2 | 0.659 |
| 02 sandboxed | cachetools-r1 | 12 | 12 | yes | 1 | 1 |  | 4 | 4/8 | 6 | 1 | 7 | 0.181 |
| 02 sandboxed | marshmallow-r1 | 1 | 1 | yes | 0 | 0 |  | 1 | 1/6 | 24 | 0 | 1 | 0.397 |
| 02 sandboxed | more-itertools-r1 | 13 | 13 | yes | 0 | 0 |  | 7 | 5/18 | 13 | 0 | 8 | 0.133 |
| 02 sandboxed | pluggy-r1 | 6 | 6 | yes | 0 | 0 |  | 4 | 3/14 | 11 | 0 | 8 | 0.118 |
| 02 sandboxed | sqlparse-r1 | 3 | 3 | yes | 0 | 0 |  | 2 | 2/5 | 28 | 0 | 3 | 0.596 |
| 02 sandboxed | tinydb-r1 | 5 | 5 | yes | 0 | 0 |  | 4 | 4/8 | 26 | 0 | 7 | 0.615 |
| 02 sandboxed | tomlkit-r1 | 19 | 19 | yes | 0 | 0 |  | 9 | 8/55 | 27 | 0 | 15 | 0.187 |
| 04-ab-test | cattrs-r1 | 5 | 5 | yes | 0 | 0 | 3 | 3 | 3/7 | 17 | 0 | 9 | 0.507 |
| 04-ab-test | cattrs-r2 | 26 | 26 | yes | 0 | 0 | 4 | 4 | 3/7 | 21 | 0 | 9 | 0.569 |
| 04-ab-test | cattrs-r3 | 6 | 6 | yes | 0 | 0 | 4 | 4 | 4/7 | 25 | 0 | 5 | 0.757 |
| 04-ab-test | cattrs-r4 | 5 | 5 | yes | 0 | 0 | 3 | 3 | 3/7 | 21 | 0 | 7 | 0.581 |
| 04-ab-test | pydantic-settings-r1 | 1 | 1 | yes | 0 | 0 | 0 | 0 | 0/6 | 10 | 0 | 8 | -0.126 |
| 04-ab-test | pydantic-settings-r2 | 1 | 1 | yes | 0 | 0 | 1 | 1 | 0/6 | 10 | 0 | 7 | -0.157 |
| 04-ab-test | pydantic-settings-r3 | 2 | 2 | yes | 0 | 0 | 1 | 1 | 0/6 | 14 | 0 | 11 | -0.159 |
| 04-ab-test | pydantic-settings-r4 | 1 | 1 | yes | 0 | 0 | 1 | 1 | 0/6 | 13 | 0 | 9 | -0.098 |
| 04-ab-test | python-dotenv-r1 | 6 | 6 | yes | 0 | 0 | 1 | 1 | 0/7 | 12 | 0 | 6 | -0.007 |
| 04-ab-test | python-dotenv-r2 | 3 | 3 | yes | 0 | 0 | 1 | 1 | 0/7 | 8 | 0 | 4 | -0.088 |
| 04-ab-test | python-dotenv-r3 | 16 | 16 | yes | 0 | 0 | 3 | 3 | 3/7 | 7 | 0 | 5 | 0.298 |
| 04-ab-test | python-dotenv-r4 | 7 | 7 | yes | 0 | 0 | 2 | 2 | 1/7 | 9 | 0 | 4 | 0.093 |
| 04-ab-test | werkzeug-r1 | 6 | 6 | yes | 0 | 0 | 3 | 3 | 1/10 | 46 | 0 | 18 | 0.135 |
| 04-ab-test | werkzeug-r2 | 4 | 4 | yes | 0 | 0 | 2 | 2 | 1/10 | 23 | 0 | 9 | 0.169 |
| 04-ab-test | werkzeug-r3 | 7 | 7 | yes | 0 | 0 | 2 | 2 | 1/10 | 33 | 0 | 12 | 0.191 |
| 04-ab-test | werkzeug-r4 | 10 | 10 | yes | 0 | 0 | 5 | 5 | 4/10 | 48 | 0 | 13 | 0.514 |
