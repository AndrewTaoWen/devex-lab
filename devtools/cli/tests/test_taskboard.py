"""The Flask example: one request, from the form the user filled in to the page they get back, is recorded in
order with the values that passed between functions."""
import os

from repomap import graph

ADD_TASK = "tests/test_app.py::test_add_task_shows_on_the_list"


def calls(repo, test):
    previous_dir = os.getcwd()
    os.chdir(repo)
    try:
        return graph.test_calls(graph.connect(), test)
    finally:
        os.chdir(previous_dir)


def test_every_test_is_recorded(taskboard):
    previous_dir = os.getcwd()
    os.chdir(taskboard)
    try:
        runs = dict(graph.connect().execute("SELECT test, steps FROM test_runs"))
    finally:
        os.chdir(previous_dir)
    assert len(runs) == 14 and runs[ADD_TASK] > 0


def test_add_task_flows_from_form_to_page(taskboard):
    """Flask calls the route; the route hands the form to the view; the view reads the form, stores the task and
    renders the page. The form as typed and the page sent back are both in the recording."""
    rows = calls(taskboard, ADD_TASK)
    order = [row["function"].split(":")[1] for row in rows]
    request = order.index("create")
    assert order[request:request + 3] == ["create", "store_and_today", "create_task"]
    assert ["read_task_form", "clean_title", "parse_due", "parse_priority", "TaskStore.add"] == \
        [name for name in order[request:] if name in ("read_task_form", "clean_title", "parse_due", "parse_priority", "TaskStore.add")]
    by_name = {row["function"].split(":")[1]: row for row in rows}
    assert "'title': '  Buy   milk '" in by_name["read_task_form"]["args"]["form"]["repr"]
    assert by_name["clean_title"]["ret"]["repr"] == "'Buy milk'"
    assert by_name["create"]["parent_seq"] is None  # called by Flask, outside the package
    assert by_name["create"]["ret"]["repr"].startswith('("<h1>Tasks</h1>')


def test_bad_date_raises_inside_and_shows_a_message(taskboard):
    rows = calls(taskboard, "tests/test_app.py::test_bad_date_is_refused")
    parse_due = next(row for row in rows if row["function"].endswith(":parse_due"))
    assert parse_due["raised"] and "FormError" in parse_due["raised"]
