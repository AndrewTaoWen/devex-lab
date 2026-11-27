"""Where tasks live: a plain in-memory list."""
from dataclasses import dataclass
from datetime import date


@dataclass
class Task:
    id: int
    title: str
    due: date | None = None
    priority: str = "normal"
    done: bool = False


PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


class TaskStore:
    def __init__(self):
        self.tasks = []

    def add(self, title, due=None, priority="normal"):
        task = Task(id=len(self.tasks) + 1, title=title, due=due, priority=priority)
        self.tasks.append(task)
        return task

    def get(self, task_id):
        return next((task for task in self.tasks if task.id == task_id), None)

    def mark_done(self, task_id):
        task = self.get(task_id)
        if task is None:
            raise KeyError(task_id)
        task.done = True
        return task

    def search(self, text):
        words = text.lower().split()
        return [task for task in self.tasks if all(word in task.title.lower() for word in words)]

    def in_order(self, tasks):
        """Open tasks first, then by priority, then the soonest due date (no date last)."""
        return sorted(tasks, key=lambda task: (task.done, PRIORITY_ORDER[task.priority], task.due or date.max))
