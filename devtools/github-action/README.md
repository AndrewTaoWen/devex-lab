# repomap GitHub Action

**Status: planned.**

On every pull request: select the tests the diff reaches, run only those, and comment the impact.

## Flow

1. Restore `.repomap/memory.json` for the PR's base commit from the Actions cache (record in full on a cache miss, or on `main` pushes).
2. `repomap.py impact` against the PR diff → selected test ids.
3. Run the selected tests. Run the full suite instead when the map is older than a set number of commits, when the diff touches conftest or test fixtures, or on a schedule, since order-dependent failures (test pollution) aren't predicted.
4. Post or update one PR comment: changed functions, callers by level, tests selected vs suite size, and a link to the exported graph view (`serve --export`).
5. Upload the refreshed memory so the next run starts warm.

## Why it matters

On werkzeug's real history, selection by changed function caught every result-changing test at 24% of the suite; on injected faults the median was 0.6%. CI time is the most visible cost repomap can cut, and the comment makes change impact visible to reviewers who never run the CLI.

## Inputs (draft)

```yaml
- uses: andrewtaowen/repo-lifetime-sim/devtools/github-action@main
  with:
    package: src/mypkg
    python: "3.12"
    full-suite-every: 20        # commits
    comment: true
```
