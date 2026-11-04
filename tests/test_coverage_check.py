import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coverage_check", ROOT / "rl-env/experiments/06-gaps-ab/coverage_check.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

SRC = '''"""module docstring"""
import os


def f(x):
    """docstring"""
    if x:
        return 1
    return 2
'''


def test_stmts_skips_docstrings_imports_and_defs():
    assert cc.stmts(SRC) == {7, 8, 9}


def test_stmts_inside_functions_only():
    assert cc.stmts("y = 1\n" + SRC, inside_functions=True) == {8, 9, 10}


def test_to_snapshot_maps_unchanged_lines_and_skips_new_ones():
    snap = "a\nb\nc\n"
    parent = "a\nNEW\nb\nc\n"  # a line added after the snapshot shifts b and c down
    mp = cc.to_snapshot(parent, snap)
    assert mp == {1: 1, 3: 2, 4: 3}
    assert 2 not in mp
