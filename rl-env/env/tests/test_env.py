import json
import subprocess

from futurebench import env


def test_prompt_fills_every_field():
    t = env.get_task("tinydb")
    for variant in ("improved", "baseline"):
        text = env.prompt(t, variant, known_failing=["tests/x.py::test_a"])
        assert "{" not in text.replace("{}", "")
        assert "/workspace" in text and "msiemens/tinydb" in text and "test_a" in text


def _repo(tmp_path):
    repo = tmp_path / "ws"
    repo.mkdir()
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a.py").write_text("x = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a(): pass\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=s", "-c", "user.email=s@s", "commit", "-qm", "s"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)
    snap = env.git(repo, "rev-parse", "HEAD").strip()
    (repo / ".git" / "futurebench.json").write_text(json.dumps({"task": "tinydb", "snapshot_commit": snap}))
    return repo, snap


def test_workspace_tree_sees_untracked_and_deleted_files(tmp_path):
    repo, snap = _repo(tmp_path)
    (repo / "pkg" / "new.py").write_text("y = 2\n")
    (repo / "tests" / "test_a.py").unlink()
    _, base, tree = env.workspace_tree(repo)
    assert base == snap
    names = env.git(repo, "diff", "--name-status", base, tree)
    assert "A\tpkg/new.py" in names and "D\ttests/test_a.py" in names
    # the real index is untouched
    assert env.git(repo, "status", "--porcelain").count("??") == 1


def test_replace_paths_swaps_and_removes(tmp_path):
    repo, snap = _repo(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "tests").mkdir()
    (dst / "tests" / "other.py").write_text("")
    (dst / "gone").mkdir()
    env.replace_paths(dst, repo, snap, ["tests", "gone"])
    assert (dst / "tests" / "test_a.py").exists() and not (dst / "tests" / "other.py").exists()
    assert not (dst / "gone").exists()
