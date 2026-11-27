"""Shared fixtures: recorded copies of the example repos (shopkit, and taskboard when Flask is installed), each in a
fresh git repo."""
import shutil, subprocess, sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1]
SHOPKIT = CLI / "examples" / "shopkit"
TASKBOARD = CLI / "examples" / "taskboard"
sys.path.insert(0, str(CLI / "src"))


def git(cwd, *a):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd, check=True, capture_output=True)


def repomap(cwd, *a):
    """Run the CLI the way a user would, without installing it."""
    return subprocess.run([sys.executable, "-m", "repomap", *a], cwd=cwd, capture_output=True, text=True,
                          env={"PYTHONPATH": str(CLI / "src"), "PATH": "/usr/bin:/bin"}, check=True).stdout


@pytest.fixture(scope="session")
def recorded(tmp_path_factory):
    d = tmp_path_factory.mktemp("shop") / "shopkit"
    shutil.copytree(SHOPKIT, d, ignore=shutil.ignore_patterns("__pycache__", ".repomap", ".pytest_cache"))
    git(d, "init", "-q")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    repomap(d, "record", "--package", "src/shopkit", "--python", sys.executable)
    return d


@pytest.fixture
def shop(recorded, tmp_path):
    """A private copy of the recorded repo, so a test can edit and refresh freely."""
    d = tmp_path / "shopkit"
    shutil.copytree(recorded, d, symlinks=True)
    return d


@pytest.fixture(scope="session")
def taskboard(tmp_path_factory):
    """The Flask to-do example, recorded with its call order and values and with the code graph built. Skipped
    where Flask is not installed."""
    pytest.importorskip("flask")
    d = tmp_path_factory.mktemp("taskboard") / "taskboard"
    shutil.copytree(TASKBOARD, d, ignore=shutil.ignore_patterns("__pycache__", ".repomap", ".pytest_cache"))
    git(d, "init", "-q")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    repomap(d, "record", "--package", "src/taskboard", "--python", sys.executable)
    repomap(d, "graph", "update")
    return d
