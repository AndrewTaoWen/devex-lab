# taskboard: a tiny Flask app

A to-do list with three pages: see the list (`GET /`, with `?q=` to search), add a task (`POST /tasks`) and mark one done (`POST /tasks/<id>/done`). It is here to show one request's whole flow, from the form a user fills in to the page they get back, with the values that pass between functions.

| File | What it does |
|---|---|
| `routes.py` | Flask calls these when a request comes in; they hand the request's values to the views |
| `views.py` | What each page does: read the form, change the store, render the page |
| `forms.py` | Turns what the user typed into clean values, or a message saying what is wrong |
| `store.py` | The tasks, in memory, and the order they are listed in |
| `render.py` | Turns tasks into the HTML the user sees, with labels like "due tomorrow" |

Record it and open the Graph tab (needs Flask and pytest in the interpreter you pass):

```
cd examples/taskboard
git init -q && git add -A && git commit -qm init     # repomap reads git for history
repomap record --package src/taskboard --python PY
repomap graph update
repomap serve --open
```
