"""The recompute rules: when a refresh of the changed files' tests is enough, and when to re-trace everything."""
from repomap.recompute import is_dependency_file, is_test_setup_file, plan


def test_small_edit_is_a_refresh():
    """Editing one file out of ten is a small change: only that file's tests need to re-run."""
    result = plan(["src/pkg/a.py"], "src/pkg", 10, 3)
    assert not result["full"] and result["package_files"] == ["src/pkg/a.py"] and result["share"] == 0.1


def test_dependency_change_forces_full():
    """Changing a dependency file (pyproject.toml, a requirements file, a lock file) means everything must be
    re-run. A package file that only happens to be named requirements.py doesn't count."""
    for path in ("pyproject.toml", "requirements-dev.txt", "requirements/test.txt", "uv.lock", "sub/setup.cfg"):
        assert is_dependency_file(path), path
        assert plan([path], "src/pkg", 10, 0)["full"], path
    assert not is_dependency_file("src/pkg/requirements.py")


def test_test_setup_change_forces_full():
    """Changing shared test setup (conftest.py, pytest.ini, fixture files) means everything must be re-run. A
    test file with 'fixtures' in its name doesn't count."""
    for path in ("tests/conftest.py", "conftest.py", "pytest.ini", "tests/fixtures/data.json"):
        assert is_test_setup_file(path), path
        result = plan([path], "src/pkg", 10, 0)
        assert result["full"] and "test setup changed" in result["reasons"][0]
    assert not is_test_setup_file("tests/test_fixtures.py")


def test_share_threshold():
    """When more than 20% of the package's files changed, everything is re-run. Exactly 20% is not enough, the
    limit can be changed, and files outside the package don't count."""
    changed = [f"src/pkg/m{i}.py" for i in range(3)]
    assert plan(changed, "src/pkg", 10, 0)["full"]  # 30% > 20%
    assert not plan(changed[:2], "src/pkg", 10, 0)["full"]  # 20% is not over
    assert not plan(changed, "src/pkg", 10, 0, max_share=0.5)["full"]
    # non-package files and non-Python files don't count towards the share
    assert not plan(["docs/a.md", "src/pkg/data.json", "tests/test_x.py"], "src/pkg", 1, 0)["full"]


def test_commit_threshold():
    """When more than 50 commits passed since the last full run, everything is re-run. Exactly 50 is not enough,
    and with no git history the rule never fires."""
    assert plan([], "src/pkg", 10, 51)["full"]
    assert not plan([], "src/pkg", 10, 50)["full"]
    assert not plan([], "src/pkg", 10, None)["full"]  # unknown (no git history) never forces a re-trace
    assert plan([], "src/pkg", 10, 6, max_commits=5)["reasons"] == ["6 commits since the last full trace (> 5)"]
