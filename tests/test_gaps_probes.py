import json, subprocess, sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "devtools" / "cli"
sys.path.insert(0, str(CLI))
import repomap_trace  # noqa: E402
import query  # noqa: E402


def flags_of(v):
    s = repomap_trace.summary()
    repomap_trace.note(s, v)
    return {k for k in s["flags"] if k not in ("nonempty", "empty")}


@pytest.mark.parametrize("value,expected", [
    ("abc", set()),
    ("١٢", {"non_ascii", "non_ascii_digit"}),
    ("café", {"non_ascii"}),
    ("+5", {"sign_prefix"}),
    ("-5", {"sign_prefix"}),
    ("0x1f", {"radix_prefix"}),
    ('"a"', {"quote"}),
    ("a\r\n", {"control_char", "edge_whitespace"}),
    (" a", {"edge_whitespace"}),
    ("a,,b", {"empty_field"}),
    ("user:@host", {"empty_field"}),
    (":80", {"empty_field"}),
])
def test_string_probes(value, expected):
    assert flags_of(value) == expected


def fake_map(tmp_path, functions):
    def summ(types, flags):
        return {"types": types, "flags": flags}
    F = {k: {"first": 1, "calls": c, "args": {a: summ(*v) for a, v in args.items()}, "returns": summ({}, {}), "raises": {}}
         for k, (c, args) in functions.items()}
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"root": "x", "tests": {}, "functions": F, "edges": []}))
    return p


def gaps(path, *args):
    out = subprocess.run([sys.executable, str(CLI / "query.py"), str(path), "gaps", *args],
                         capture_output=True, text=True, check=True).stdout
    return out.splitlines()


@pytest.fixture
def mapfile(tmp_path):
    return fake_map(tmp_path, {
        "pkg/a.py:parse": (5, {"s": ({"str": 3}, {"nonempty": 3, "quote": 1})}),
        "pkg/a.py:_helper": (9, {"n": ({"int": 2}, {"positive": 2})}),
        "pkg/_private.py:run": (1, {"s": ({"str": 1}, {"empty": 1})}),
        "pkg/a.py:outer.<locals>.inner": (1, {"s": ({"str": 1}, {"nonempty": 1})}),
    })


def test_basic_matches_old_probes(mapfile):
    assert gaps(mapfile, "--basic") == [
        "pkg/a.py:_helper: n never zero/negative",
        "pkg/a.py:outer.<locals>.inner: s never empty",
        "pkg/a.py:parse: s never empty",
    ]


def test_rich_probes_only_use_flags_seen_in_the_map(mapfile):
    # "quote" was recorded somewhere, so it is probed; sign_prefix never was, so it is not
    assert "pkg/a.py:outer.<locals>.inner: s never empty/quote" in gaps(mapfile)
    assert not any("sign_prefix" in line for line in gaps(mapfile))


def test_public_drops_private_and_nested(mapfile):
    assert gaps(mapfile, "--public") == ["pkg/a.py:parse: s never empty"]


def test_limit_keeps_most_called(mapfile):
    assert gaps(mapfile, "--basic", "--limit", "1") == ["pkg/a.py:_helper: n never zero/negative"]


def test_is_public():
    assert query.is_public("pkg/a.py:Cls.__init__")
    assert not query.is_public("pkg/a.py:Cls._x")
    assert not query.is_public("pkg/_mod.py:f")
