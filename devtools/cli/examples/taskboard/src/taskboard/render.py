"""Turning tasks into the HTML the user sees."""
from html import escape as html_escape


def escape(text):
    return html_escape(text, quote=False)


def render_page(tasks, today, message=None, error=None, query=""):
    open_count = sum(not task.done for task in tasks)
    parts = ["<h1>Tasks</h1>", f"<p class='count'>{open_count} open</p>"]
    if message:
        parts.append(f"<p class='message'>{escape(message)}</p>")
    if error:
        parts.append(f"<p class='error'>{escape(error)}</p>")
    if query:
        parts.append(f"<p class='query'>Matching '{escape(query)}'</p>")
    parts.append("<ul>" + "".join(render_task(task, today) for task in tasks) + "</ul>")
    return "\n".join(parts)


def render_task(task, today):
    label = due_label(task.due, today)
    flags = (" [done]" if task.done else "") + (" !" if task.priority == "high" else "")
    return f"<li>{escape(task.title)}{flags}{' (' + label + ')' if label else ''}</li>"


def due_label(due, today):
    if due is None:
        return ""
    days = (due - today).days
    if days < 0:
        return f"overdue by {-days} day{'s' if days < -1 else ''}"
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    return f"due in {days} days"
