"""Turning what the user typed into clean values, or a message saying what is wrong."""
from datetime import date


class FormError(ValueError):
    pass


def read_task_form(form, today):
    """{'title', 'due', 'priority'} as typed -> (title, due date or None, priority)."""
    return clean_title(form.get("title", "")), parse_due(form.get("due", ""), today), parse_priority(form.get("priority", ""))


def clean_title(text):
    title = " ".join(text.split())
    if not title:
        raise FormError("Give the task a title.")
    if len(title) > 80:
        raise FormError("Keep the title under 80 characters.")
    return title


def parse_due(text, today):
    """'' -> None, 'today', 'tomorrow', or YYYY-MM-DD."""
    text = text.strip().lower()
    if not text:
        return None
    if text == "today":
        return today
    if text == "tomorrow":
        return date.fromordinal(today.toordinal() + 1)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise FormError(f"'{text}' is not a date. Use YYYY-MM-DD, 'today' or 'tomorrow'.") from None


def parse_priority(text):
    priority = text.strip().lower() or "normal"
    if priority not in ("high", "normal", "low"):
        raise FormError(f"Priority must be high, normal or low, not '{priority}'.")
    return priority
