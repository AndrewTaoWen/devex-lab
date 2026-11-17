"""Landmarks: the few functions to keep labelled at every zoom level, so a reader always knows where they are.

Ranked by reach (how many distinct functions run underneath them in the tests), not by how many places call
them: a pipeline like a request dispatcher should outrank a small helper that everything calls. Functions that
tests or package users call directly (entry points) get a boost, since that is where a reader starts.

Override in .repomap/landmarks.toml:
    count  = 8                         # how many (default: 1 per 100 functions, between 5 and 10)
    pin    = ["pkg/app.py:App.run"]    # always landmarks, first, in this order
    add    = ["pkg/db.py:connect"]     # always landmarks, ranked with the rest
    remove = ["pkg/util.py:helper"]    # never landmarks
"""
import tomllib
from pathlib import Path

from . import graph

CONFIG = Path(".repomap/landmarks.toml")
ENTRY_BOOST = 1.5


def default_count(functions):
    return max(5, min(10, round(len(functions) / 100)))


def load_config(path=CONFIG):
    path = Path(path)
    if not path.exists():
        return {}
    config = tomllib.loads(path.read_text())
    for key in ("pin", "add", "remove"):
        if not isinstance(config.get(key, []), list):
            raise ValueError(f"{path}: {key} must be a list of function keys")
    return config


def score(row):
    entry = row["references_from_tests"] > 0 or graph.is_public(row["key"])
    return row["reach"] * (ENTRY_BOOST if entry else 1)


def pick(rows, config=None):
    """rows: graphmetrics rows (with references_from_tests). Returns [{key, score, why}] in order."""
    config = config or {}
    by_key = {row["key"]: row for row in rows}
    count = config.get("count") or default_count(rows)
    removed = set(config.get("remove", []))
    pinned = [key for key in config.get("pin", []) if key in by_key and key not in removed]
    added = [key for key in config.get("add", []) if key in by_key and key not in removed and key not in pinned]
    ranked = sorted((row for row in rows if row["kind"] == "function" and row["key"] not in removed
                     and row["key"] not in pinned and row["key"] not in added and row["reach"] > 0),
                    key=lambda row: (-score(row), -row["tests"], row["key"]))
    chosen = [(key, "pinned") for key in pinned]
    rest = sorted(added, key=lambda key: (-score(by_key[key]), key))
    automatic = [row["key"] for row in ranked[:max(0, count - len(pinned) - len(rest))]]
    for key in sorted(rest + automatic, key=lambda key: (-score(by_key[key]), key)):
        chosen.append((key, "added" if key in added else "reach"))
    return [dict(key=key, score=round(score(by_key[key]), 2), reach=by_key[key]["reach"], why=why)
            for key, why in chosen]
