"""The MCP server, driven over real stdio the way Claude Code or another client would drive it."""
import json, sys

import anyio
import pytest

from conftest import CLI

mcp = pytest.importorskip("mcp")
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

TOOLS = {"impact", "tests_for", "callers", "values", "gaps", "status", "refresh", "record"}


def session(repo, fn):
    """Start `repomap-mcp --root repo`, run fn(session), return its result."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "repomap.mcp_server", "--root", str(repo)],
                                   env={"PYTHONPATH": str(CLI / "src"), "PATH": "/usr/bin:/bin"})

    async def go():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)
    return anyio.run(go)


async def call(s, name, **args):
    res = await s.call_tool(name, args)
    text = "".join(c.text for c in res.content if getattr(c, "text", None))
    if res.is_error:
        return {"error": text}
    return res.structured_content.get("result", res.structured_content) if res.structured_content else json.loads(text)


def test_lists_tools_with_read_only_hints(recorded):
    tools = session(recorded, lambda s: s.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    ro = {t.name for t in tools if t.annotations and t.annotations.read_only_hint}
    assert ro == TOOLS - {"refresh", "record"}


def test_edit_impact_refresh_cycle(shop):
    p = shop / "src/shopkit/money.py"
    p.write_text(p.read_text().replace("rounding=ROUND_HALF_UP", 'rounding="ROUND_DOWN"'))

    async def flow(s):
        before = await call(s, "impact")
        refreshed = await call(s, "refresh")
        after = await call(s, "impact")  # memory reloaded after refresh, so the diff is now empty
        return before, refreshed, after
    before, refreshed, after = session(shop, flow)
    assert before["changed"] == ["shopkit/money.py:round_cents"] and len(before["tests"]) == 6
    assert before["pytest_command"].endswith("tests/test_money.py::test_round_half_up")
    assert refreshed["changed_files"] == ["src/shopkit/money.py"]
    assert after["changed"] == [] and after["files_changed_since_record"] == []


def test_lookups(recorded):
    async def flow(s):
        return (await call(s, "impact", targets=["receipt"]), await call(s, "callers", function="round_cents"),
                await call(s, "tests_for", location="src/shopkit/money.py:6"), await call(s, "values", function="round_cents"),
                await call(s, "gaps", module="cart"), await call(s, "status"))
    imp, cal, tf, val, gaps, st = session(recorded, flow)
    assert imp["changed"] == ["shopkit/checkout.py:receipt"] and imp["unmatched"] == []
    assert cal["callers"][0] == ["shopkit/checkout.py:total", "shopkit/tax.py:tax_for"]
    assert "tests/test_money.py::test_round_half_up" in tf["tests"]
    assert val[0]["args"]["amount"]["never"] == ["negative"]
    assert gaps[0]["function"] == "shopkit/cart.py:Cart.add"
    assert st["tests"] == 18


def test_no_memory_is_a_readable_error(tmp_path):
    async def flow(s):
        return await call(s, "status"), await call(s, "impact")
    st, imp = session(tmp_path, flow)
    assert st["memory"] is None
    assert "record" in imp["error"]
