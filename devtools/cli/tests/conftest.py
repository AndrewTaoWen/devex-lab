"""Shared fixture: a recorded copy of the shopkit example repo, in a fresh git repo."""
import shutil, subprocess, sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1]
SHOPKIT = CLI / "examples" / "shopkit"
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
