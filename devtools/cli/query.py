#!/usr/bin/env python3
"""Run repomap's query from a checkout: python devtools/cli/query.py MAP fn|callers|tests|gaps [ARG] [--json]

Installed, the same thing is `repomap query KIND [ARG]` (it reads .repomap/memory.json).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != HERE]
sys.path.insert(0, str(HERE / "src"))

from repomap.query import main  # noqa: E402

main()
