"""repomap's memory snapshot must not copy the memory file or bytecode into git objects."""
import importlib.util, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "devtools" / "cli"


def load_cli():
    # the CLI lives at devtools/cli/repomap.py, or at src/repomap/cli.py once packaged
    path = next(p for p in (ROOT / "src" / "repomap" / "cli.py", ROOT / "repomap.py") if p.exists())
    spec = importlib.util.spec_from_file_location("repomap_cli_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(cwd, *a):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def test_snapshot_skips_memory_and_pycache(tmp_path, monkeypatch):
    (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "pkg" / "new.py").write_text("y = 2\n")  # untracked source: must be in the snapshot
    (tmp_path / "pkg" / "__pycache__" / "mod.cpython-313.pyc").write_bytes(b"\0")
    (tmp_path / ".repomap").mkdir()
    (tmp_path / ".repomap" / "memory.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    commit = load_cli().worktree_snapshot(keep=False)
    files = git(tmp_path, "ls-tree", "-r", "--name-only", commit).split()
    assert files == ["pkg/mod.py", "pkg/new.py"]
