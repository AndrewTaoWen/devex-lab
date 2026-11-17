"""When to recompute the graph. It comes from running tests, so recomputing means re-running tests, and the
question is how many.

Every update (cheap):   re-read the source of changed files, and re-trace only the tests that run them
                        ('repomap refresh').
Full re-trace (slow):   when what the tests run could have shifted everywhere, not just in the changed files:
  - dependencies changed (pyproject.toml, setup.py/cfg, requirements*.txt, lock files)
  - test setup changed (any conftest.py, pytest.ini, tox.ini, files under a fixtures/ directory)
  - more than MAX_SHARE of the package's files changed since the last full trace
  - more than MAX_COMMITS commits since the last full trace

Defaults start at 20% and 50 commits. On werkzeug a map left stale for 54 commits still caught 89% of
result-changing tests, so 50 keeps recall near the refreshed map's; tune both with real history.
"""
from fnmatch import fnmatch
from pathlib import PurePosixPath

MAX_SHARE = 0.2
MAX_COMMITS = 50
DEPENDENCY_FILES = ("pyproject.toml", "setup.py", "setup.cfg", "requirements*.txt", "requirements/*.txt",
                    "*.lock", "Pipfile", "constraints*.txt")
TEST_SETUP_FILES = ("conftest.py", "pytest.ini", "tox.ini")


def is_dependency_file(path):
    name = PurePosixPath(path).name
    return any(fnmatch(name, pattern) or fnmatch(path, pattern) or fnmatch(path, f"*/{pattern}")
               for pattern in DEPENDENCY_FILES)


def is_test_setup_file(path):
    parts = PurePosixPath(path).parts
    return parts[-1] in TEST_SETUP_FILES or "fixtures" in parts[:-1]


def plan(changed, package, n_package_files, commits_since_full, max_share=MAX_SHARE, max_commits=MAX_COMMITS):
    """Decide between a refresh and a full re-trace.

    changed: repo-relative paths that differ from the last full trace (edited, added or deleted).
    package: the package directory ("src/pkg"); n_package_files: how many .py files it has now.
    Returns {"full": bool, "reasons": [str], "package_files": [paths], "share": float, "commits": int}.
    """
    prefix = package.rstrip("/") + "/"
    package_changed = sorted(path for path in changed if path.startswith(prefix) and path.endswith(".py"))
    dependencies = sorted(path for path in changed if is_dependency_file(path))
    setup = sorted(path for path in changed if is_test_setup_file(path))
    share = len(package_changed) / max(n_package_files, 1)
    reasons = []
    if dependencies:
        reasons.append(f"dependencies changed: {', '.join(dependencies)}")
    if setup:
        reasons.append(f"test setup changed: {', '.join(setup)}")
    if share > max_share:
        reasons.append(f"{len(package_changed)} of {n_package_files} package files changed "
                       f"({share:.0%} > {max_share:.0%})")
    if commits_since_full is not None and commits_since_full > max_commits:
        reasons.append(f"{commits_since_full} commits since the last full trace (> {max_commits})")
    return dict(full=bool(reasons), reasons=reasons, package_files=package_changed, share=round(share, 3),
                commits=commits_since_full)
