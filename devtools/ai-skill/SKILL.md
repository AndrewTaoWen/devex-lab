---
name: repomap
description: Check what a code change affects and run only the tests it reaches, using repomap's traced test map. Use before editing a function in a Python repo, after making changes, or when hunting for untested inputs.
---

# repomap: change impact for Python repos

repomap remembers which lines each test runs, who calls whom, and what values every function saw. Lookups don't run anything and take under a second.

Set `RM` to the CLI: `RM="python devtools/cli/repomap.py"` in this repo, or `RM=repomap.py` if it is on the path. Query the value memory with `python devtools/cli/query.py .repomap/memory.json ...`.

## 1. Make sure there is a memory

If `.repomap/memory.json` does not exist, record once from the repo root:

```
$RM record --package src/<pkg>      # or the package dir; omit --package to let it guess
```

`$RM status` shows which commit the memory was traced on and whether files changed since.

## 2. Before editing a function

```
$RM impact <Function.or.method>      # or path/to/file.py:LINE
```

Read the callers (direct and level 2+) and the test count. If a change will reach many callers, say so to the user before making it, and keep the public behaviour those callers rely on.

## 3. After editing

```
$RM impact                           # impact of the uncommitted diff
```

Run exactly the tests it lists with pytest (pass the node ids). If the diff touches code no test runs ("0 tests"), say that plainly: nothing verifies the change, so write a test. When the edit is done, update the memory:

```
$RM refresh
```

## 4. Hardening and review

```
python devtools/cli/query.py .repomap/memory.json gaps [module]
python devtools/cli/query.py .repomap/memory.json fn <name>
```

`gaps` lists arguments never exercised with empty, zero, negative or None values. Those are the inputs agents most often fail to anticipate. For each function you touched, check its gaps and either add a test for the missing case or explain why it can't happen.

## Limits

- Python 3.12+ with pytest only.
- Tests that fail only because an earlier test left state behind (test pollution) aren't predicted. When in doubt before a merge, run the full suite once.
- A memory recorded long ago still works but drifts; refresh after large changes.
