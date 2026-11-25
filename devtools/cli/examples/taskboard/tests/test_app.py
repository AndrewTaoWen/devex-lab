"""Requests a user would make, through Flask's test client."""
from datetime import date

import pytest

from taskboard import create_app


@pytest.fixture
def client():
    return create_app(today=date(2026, 11, 25)).test_client()


def page(response):
    return response.get_data(as_text=True)


def test_add_task_shows_on_the_list(client):
    """Typing a task into the form adds it, and the page that comes back lists it with its due label."""
    response = client.post("/tasks", data={"title": "  Buy   milk ", "due": "tomorrow", "priority": "high"})
    assert response.status_code == 201
    assert "Added 'Buy milk'." in page(response)
    assert "<li>Buy milk ! (due tomorrow)</li>" in page(response)
    assert "1 open" in page(client.get("/"))


def test_empty_title_is_refused(client):
    """A blank title shows a message and adds nothing."""
    response = client.post("/tasks", data={"title": "   "})
    assert response.status_code == 400
    assert "Give the task a title." in page(response)
    assert "0 open" in page(response)


def test_bad_date_is_refused(client):
    """A due date that is not a date says how to write one."""
    response = client.post("/tasks", data={"title": "Call mum", "due": "next week"})
    assert response.status_code == 400
    assert "is not a date" in page(response)


def test_bad_priority_is_refused(client):
    response = client.post("/tasks", data={"title": "Call mum", "priority": "urgent"})
    assert response.status_code == 400
    assert "not 'urgent'" in page(response)


def test_list_puts_open_and_urgent_first(client):
    """Open tasks come before done ones; high priority first; then the soonest due date."""
    client.post("/tasks", data={"title": "Water plants", "due": "2026-11-30"})
    client.post("/tasks", data={"title": "Pay rent", "due": "2026-11-20", "priority": "high"})
    client.post("/tasks", data={"title": "Read book"})
    client.post("/tasks/1/done")
    items = page(client.get("/")).split("<li>")[1:]
    assert [item.split("</li>")[0] for item in items] == [
        "Pay rent ! (overdue by 5 days)", "Read book", "Water plants [done] (due in 5 days)"]


def test_mark_done(client):
    client.post("/tasks", data={"title": "Book dentist", "due": "today"})
    response = client.post("/tasks/1/done")
    assert "Done: 'Book dentist'." in page(response)
    assert "0 open" in page(response)


def test_mark_done_unknown_task(client):
    response = client.post("/tasks/9/done")
    assert response.status_code == 404
    assert "No task 9." in page(response)


def test_search(client):
    """Searching keeps tasks whose title has every word, in any case."""
    client.post("/tasks", data={"title": "Buy milk"})
    client.post("/tasks", data={"title": "Buy bread"})
    body = page(client.get("/?q=MILK buy"))
    assert "Matching 'MILK buy'" in body and "Buy milk" in body and "Buy bread" not in body
