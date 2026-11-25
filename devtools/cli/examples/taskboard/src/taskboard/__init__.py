"""taskboard: a tiny Flask to-do list, used to show one request's flow from form to page."""
from datetime import date

from flask import Flask

from .store import TaskStore


def create_app(today=None):
    """The app, with an empty task list. 'today' fixes the date so due labels are the same in every test run."""
    from .routes import pages
    app = Flask(__name__)
    app.config["STORE"] = TaskStore()
    app.config["TODAY"] = today or date.today()
    app.register_blueprint(pages)
    return app
