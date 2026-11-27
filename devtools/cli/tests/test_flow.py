"""The Flow view: one test's calls as boxes and arrows, stepping through them with the values on each arrow."""
import subprocess, sys

import pytest

from conftest import CLI, repomap
from test_serve_graph import free_port, page_check, server  # noqa: F401  (server is a fixture)

RECEIPT = "tests/test_checkout.py::test_receipt"
ADD_TASK = "tests/test_app.py::test_add_task_shows_on_the_list"


@pytest.fixture
def task_server(taskboard):
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "repomap", "serve", "--port", str(port)], cwd=taskboard,
                            env={"PYTHONPATH": str(CLI / "src"), "PATH": "/usr/bin:/bin"}, stdout=subprocess.PIPE, text=True)
    yield proc.stdout.readline().split(": ", 1)[1].split()[0]
    proc.terminate()
    proc.wait()


def test_page_opens_on_flow_and_follows_receipt(server):
    """A fresh page opens on the Flow view, full width. test_receipt first builds a catalog and a cart, then calls
    receipt, which calls total: the 8th call. There the current box is total and its arrow shows what went in and
    what came back. The genexpr inside Cart.subtotal folds into it, so 17 of the 18 recorded calls are listed."""
    report = page_check(server, "flow", RECEIPT, "7")
    assert report["opened"] == "flow" and report["sideHidden"]
    assert report["boxes"][0] == RECEIPT and "shopkit/checkout.py:receipt" in report["boxes"]
    assert report["current"] == ["shopkit/checkout.py:total"]
    arg, ret = report["onArrow"]
    assert arg.startswith("→ ") and "region=" in arg and ret.startswith("← ")
    assert report["counter"].startswith("call 8 of 17") and report["calls"] == 17
    assert "receipt → total(" in report["step"]


def test_add_task_flow_shows_the_form_reaching_the_parser(task_server):
    """In the Flask app, following 'add a task' to its 6th call lands on read_task_form, called by the view with
    the form as the user typed it. Import-time module code is left out of the boxes."""
    report = page_check(task_server, "flow", ADD_TASK, "5")
    assert report["current"] == ["taskboard/forms.py:read_task_form"]
    assert "create_task → read_task_form(form=" in report["step"] and "'priority': 'high'" in report["step"]
    assert not any("<module" in box for box in report["boxes"])
    assert report["arrows"] >= len(report["boxes"]) - 1
