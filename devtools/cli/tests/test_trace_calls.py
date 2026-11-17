"""The tracer's per-test call order: stack handling for returns, exceptions and generators, the row cap, and the
record flags that turn values off or shorten them."""
import json, subprocess, sys, textwrap

from conftest import CLI, git, repomap

PKG = {
    "src/demo/__init__.py": "",
    "src/demo/core.py": """
        def leaf(x):
            return x * 2

        def middle(x):
            return leaf(x) + 1

        def boom():
            leaf(1)
            raise ValueError("no")

        def safe():
            try:
                boom()
            except ValueError:
                pass
            return leaf(3)

        def gen(n):
            for i in range(n):
                yield leaf(i)

        def use_gen():
            return sum(gen(3))

        def loop(n):
            for i in range(n):
                leaf(i)

        def long_text():
            return "x" * 500
        """,
    "tests/test_demo.py": """
        from demo.core import middle, safe, use_gen, loop, long_text

        def test_middle():
            assert middle(2) == 5

        def test_safe():
            assert safe() == 6

        def test_gen():
            assert use_gen() == 6

        def test_loop():
            loop(10)

        def test_long():
            assert long_text()
        """,
}


def make(tmp_path, *flags):
    d = tmp_path / "demo"
    for rel, src in PKG.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src))
    git(d, "init", "-q")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    repomap(d, "record", "--package", "src/demo", "--python", sys.executable, *flags)
    return json.loads((d / ".repomap/calls.json").read_text())


def fns(calls, test):
    return [(r[1], r[2], r[3].split(":")[1]) for r in calls[f"tests/test_demo.py::{test}"]["rows"]]


def test_nesting_and_parents(tmp_path):
    """When middle calls leaf, the recorded order shows middle first, then leaf one level deeper with middle as
    its caller, plus the argument and return value."""
    calls = make(tmp_path)
    assert fns(calls, "test_middle") == [(1, None, "middle"), (2, 0, "leaf")]
    row = calls["tests/test_demo.py::test_middle"]["rows"][1]
    assert row[4] == {"x": {"type": "int", "repr": "2"}} and row[5] == {"type": "int", "repr": "4"}


def test_exception_pops_the_stack(tmp_path):
    """When boom raises and safe catches it, the next call safe makes is recorded as safe's call, not boom's.
    The row for boom notes that it raised ValueError."""
    calls = make(tmp_path)
    # boom raises; the leaf call after the except must hang off safe again, not off boom
    assert fns(calls, "test_safe") == [(1, None, "safe"), (2, 0, "boom"), (3, 1, "leaf"), (2, 0, "leaf")]
    assert calls["tests/test_demo.py::test_safe"]["rows"][1][6] == "ValueError"


def test_generator_resumes_its_own_row(tmp_path):
    """A generator gets one row however many times it pauses and resumes, and the calls it makes each time hang
    off that one row."""
    calls = make(tmp_path)
    rows = fns(calls, "test_gen")
    gen_seq = [i for i, r in enumerate(rows) if r[2] == "gen"]
    assert len(gen_seq) == 1  # one row for the generator, however many times it resumes
    leaves = [r for r in rows if r[2] == "leaf"]
    assert len(leaves) == 3 and all(r[1] == gen_seq[0] for r in leaves)


def test_cap_and_truncated(tmp_path):
    """With a cap of 5 rows, a test that makes 11 calls keeps 5 rows, still counts all 11, and is marked as cut
    short. A test under the cap is not marked."""
    calls = make(tmp_path, "--steps", "5")
    c = calls["tests/test_demo.py::test_loop"]
    assert len(c["rows"]) == 5 and c["steps"] == 11 and c["truncated"] is True
    assert calls["tests/test_demo.py::test_middle"]["truncated"] is False


def test_no_values_and_repr_length(tmp_path):
    """With --no-values no arguments or return values are stored. With --repr-len 20, long values are shortened
    to at most 20 characters."""
    calls = make(tmp_path, "--no-values")
    assert all(r[4] is None and r[5] is None for c in calls.values() for r in c["rows"])
    calls = make(tmp_path / "b", "--repr-len", "20")
    ret = calls["tests/test_demo.py::test_long"]["rows"][0][5]
    assert len(ret["repr"]) <= 20 and "..." in ret["repr"]


def test_steps_zero_turns_call_order_off(tmp_path):
    """--steps 0 turns call-order recording off completely."""
    assert make(tmp_path, "--steps", "0") == {}


def test_refresh_keeps_call_order_current(tmp_path):
    """After a function is edited, refresh re-records the call order of the tests that ran it, and keeps the
    rows of the tests it didn't need to re-run."""
    make(tmp_path)
    d = tmp_path / "demo"
    p = d / "src/demo/core.py"
    p.write_text(p.read_text().replace("return leaf(x) + 1", "return leaf(x) + leaf(0) + 1"))
    repomap(d, "refresh")
    calls = json.loads((d / ".repomap/calls.json").read_text())
    assert fns(calls, "test_middle") == [(1, None, "middle"), (2, 0, "leaf"), (2, 0, "leaf")]
    assert "tests/test_demo.py::test_safe" in calls  # tests that weren't re-traced keep their rows
