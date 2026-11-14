"""The recompute rules: when a refresh of the changed files' tests is enough, and when to re-trace everything."""
from repomap.recompute import is_dependency_file, is_test_setup_file, plan


def test_small_edit_is_a_refresh():
    p = plan(["src/pkg/a.py"], "src/pkg", 10, 3)
    assert not p["full"] and p["package_files"] == ["src/pkg/a.py"] and p["share"] == 0.1


def test_dependency_change_forces_full():
    for f in ("pyproject.toml", "requirements-dev.txt", "requirements/test.txt", "uv.lock", "sub/setup.cfg"):
        assert is_dependency_file(f), f
        assert plan([f], "src/pkg", 10, 0)["full"], f
    assert not is_dependency_file("src/pkg/requirements.py")


def test_test_setup_change_forces_full():
    for f in ("tests/conftest.py", "conftest.py", "pytest.ini", "tests/fixtures/data.json"):
        assert is_test_setup_file(f), f
        p = plan([f], "src/pkg", 10, 0)
        assert p["full"] and "test setup changed" in p["reasons"][0]
    assert not is_test_setup_file("tests/test_fixtures.py")


def test_share_threshold():
    changed = [f"src/pkg/m{i}.py" for i in range(3)]
    assert plan(changed, "src/pkg", 10, 0)["full"]  # 30% > 20%
    assert not plan(changed[:2], "src/pkg", 10, 0)["full"]  # 20% is not over
    assert not plan(changed, "src/pkg", 10, 0, max_share=0.5)["full"]
    # non-package files and non-Python files don't count towards the share
    assert not plan(["docs/a.md", "src/pkg/data.json", "tests/test_x.py"], "src/pkg", 1, 0)["full"]


def test_commit_threshold():
    assert plan([], "src/pkg", 10, 51)["full"]
    assert not plan([], "src/pkg", 10, 50)["full"]
    assert not plan([], "src/pkg", 10, None)["full"]  # unknown (no git history) never forces a re-trace
    assert plan([], "src/pkg", 10, 6, max_commits=5)["reasons"] == ["6 commits since the last full trace (> 5)"]
