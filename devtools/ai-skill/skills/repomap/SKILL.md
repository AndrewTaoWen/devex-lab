---
name: repomap
description: Check what a code change affects and run only the tests it reaches, using repomap's traced map of the test suite. Use in a Python repo with pytest before editing a function, after making changes, and when hardening or reviewing code for untested inputs.
---

# repomap: change impact for Python repos

repomap remembers, from one traced run of the test suite, which lines each test ran, who calls whom, and what values every function was called with. Lookups read only that memory and take under a second, so use them freely.

## Which interface to use

- **MCP tools** (preferred): if the `repomap` MCP server is connected you have tools named `impact`, `tests_for`, `callers`, `values`, `gaps`, `status`, `refresh` and `record` (your client may prefix them, e.g. `mcp__repomap__impact`). They return JSON.
- **CLI fallback**: otherwise use the `repomap` command (`pip install "repomap[mcp]"`), or `python devtools/cli/repomap.py` inside the devex-lab repo. Add `--json` to any command for the same JSON the tools return.

| Step | MCP tool | CLI |
|---|---|---|
| Is there a memory? | `status` | `repomap status --json` |
| Record once | `record(package?)` | `repomap record [--package src/pkg]` |
| Before an edit | `impact(targets=["Name"])` | `repomap impact Name --json` |
| After an edit | `impact()` | `repomap impact --json` |
| Update memory | `refresh` | `repomap refresh` |
| Tests for a line | `tests_for("path.py:LINE")` | `repomap query tests path.py:LINE` |
| Callers | `callers("Name")` | `repomap query callers Name` |
| Values seen | `values("Name")` | `repomap query fn Name` |
| Untested inputs | `gaps("module")` | `repomap query gaps module` |

## 1. Make sure there is a memory

Call `status`. If there is no memory, `record` once from the repo root. Recording runs the whole suite (about the time of a normal test run plus tracing overhead), so tell the user before you start it on a large repo. Pass the package directory if repomap can't guess it (`src/<pkg>` or `<pkg>`). By default it uses the repo's `.venv` interpreter if there is one.

If `status` lists many changed files, `refresh` before relying on lookups.

## 2. Before editing a function

Call `impact` with the function's name (or `path/to/file.py:LINE`). Read:

- **callers**: direct callers, then their callers. These are what your edit can break. Keep the behaviour they rely on, or update them too.
- **tests**: how many tests run it. If it is many, or the callers span several modules, tell the user the blast radius before making a behaviour change.
- **unmatched**: names that matched nothing. Check the spelling, or the function is new.
- **untested**: functions no test runs. The memory can't say what depends on them.

## 3. After editing

Call `impact` with no targets. That diffs your uncommitted changes against the exact tree the memory was recorded on.

- Run exactly the listed tests with pytest (`pytest_command` in the tool result, or pass the node ids). Don't run the full suite for every small edit.
- If a changed function is in `untested`, or the result selects 0 tests for a real change, say so plainly: nothing verifies that change. Write a test for it.
- When the edit is done and its tests pass, call `refresh` so the next lookup is current. It re-traces only the affected tests and takes seconds.

## 4. Hardening and review

For each function you touched (or are reviewing), call `gaps` on its module and `values` on the function. `gaps` lists arguments the suite never fed an edge value: empty, zero or negative, and for strings things like non-ASCII text, quotes, a sign or `0x` prefix, or an empty field between separators. In this project's experiments those are exactly the inputs agents failed to anticipate. For a whole-package sweep, pass `public=True, limit=20` (CLI: `--public --limit 20`) to see the most-called public functions first. For each gap, either add a test for the missing case or explain why the input can't happen.

## Limits

- Python 3.12+ and pytest only.
- A test that fails only because an earlier test left state behind (test pollution) isn't predicted. Before a merge, run the full suite once.
- Changes outside any function (constants, module-level code) select every test that runs code in that file, which is safe but broad.
- An old memory still works but drifts. `refresh` after edits, and re-`record` after large refactors.
