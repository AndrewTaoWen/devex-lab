"""The web side: Flask calls these when a request comes in."""
from flask import Blueprint, current_app, request

from . import views

pages = Blueprint("pages", __name__)


def store_and_today():
    return current_app.config["STORE"], current_app.config["TODAY"]


@pages.get("/")
def index():
    store, today = store_and_today()
    return views.show_list(store, today, request.args.get("q", "").strip())


@pages.post("/tasks")
def create():
    store, today = store_and_today()
    return views.create_task(store, request.form.to_dict(), today)


@pages.post("/tasks/<int:task_id>/done")
def done(task_id):
    store, today = store_and_today()
    return views.complete_task(store, task_id, today)
