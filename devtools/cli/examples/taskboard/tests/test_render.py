"""Due labels, read straight from the function."""
import pytest
from datetime import date

from taskboard.render import due_label

TODAY = date(2026, 11, 25)


@pytest.mark.parametrize("due, label", [
    (None, ""), (date(2026, 11, 25), "due today"), (date(2026, 11, 26), "due tomorrow"),
    (date(2026, 11, 28), "due in 3 days"), (date(2026, 11, 24), "overdue by 1 day"), (date(2026, 11, 20), "overdue by 5 days")])
def test_due_label(due, label):
    assert due_label(due, TODAY) == label
