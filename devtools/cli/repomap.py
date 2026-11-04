#!/usr/bin/env python3
"""Run repomap from a checkout without installing it: python devtools/cli/repomap.py ...

Same as the `repomap` command that `pip install ./devtools/cli` puts on your path.
"""
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent / "src" / "repomap"

if __name__ == "__main__":
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != PKG.parent.parent]  # this file shadows the package
    sys.path.insert(0, str(PKG.parent))
    from repomap.cli import main
    main()
else:
    # Imported as `repomap` because this directory is on sys.path (e.g. `python -m repomap` run from here):
    # stand in for the real package so `repomap.cli`, `repomap.mcp_server` etc. still resolve.
    __path__ = [str(PKG)]
    exec((PKG / "__init__.py").read_text())
