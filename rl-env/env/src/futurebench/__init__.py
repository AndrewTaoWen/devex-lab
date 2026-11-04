"""futurebench: harden a repo snapshot blind, get scored on the repo's own future tests."""
from .manifest import Task, get_task, list_tasks  # noqa: F401
from .reward import Parts, Reward, Weights  # noqa: F401

__version__ = "0.1.0"
