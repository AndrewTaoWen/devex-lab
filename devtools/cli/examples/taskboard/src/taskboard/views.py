"""What each page does, without Flask: the routes pass in the store and what the user sent."""
from .forms import FormError, read_task_form
from .render import render_page


def show_list(store, today, query=""):
    tasks = store.search(query) if query else store.tasks
    return render_page(store.in_order(tasks), today, query=query)


def create_task(store, form, today):
    try:
        title, due, priority = read_task_form(form, today)
    except FormError as problem:
        return render_page(store.in_order(store.tasks), today, error=str(problem)), 400
    task = store.add(title, due, priority)
    return render_page(store.in_order(store.tasks), today, message=f"Added '{task.title}'."), 201


def complete_task(store, task_id, today):
    try:
        task = store.mark_done(task_id)
    except KeyError:
        return render_page(store.in_order(store.tasks), today, error=f"No task {task_id}."), 404
    return render_page(store.in_order(store.tasks), today, message=f"Done: '{task.title}'."), 200
