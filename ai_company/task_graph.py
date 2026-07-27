"""
Task Graph
==========
A directed acyclic graph (DAG) of tasks.

Each task knows which tasks it depends on. The graph can tell you,
at any moment, which tasks are "ready" (all dependencies finished),
so the scheduler knows what it can run in parallel.

Key features:
- Cycle detection (a task can't indirectly depend on itself)
- Status tracking: PENDING -> RUNNING -> DONE / FAILED
- Failure propagation: if a task fails permanently, everything
  downstream of it is marked BLOCKED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

class TaskStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"

@dataclass
class Task:
    """One unit of work assigned to one agent role."""
    task_id: str
    title: str
    description: str
    role: str
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    attempts: int = 0

    def __repr__(self) -> str:
        return f"Task({self.task_id}, role={self.role}, status={self.status.value})"

class CycleError(Exception):
    """Raised when the task graph contains a dependency cycle."""

class TaskGraph:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}

    def add_task(self, task: Task) -> None:
        if task.task_id in self.tasks:
            raise ValueError(f"Duplicate task id: {task.task_id}")
        self.tasks[task.task_id] = task

    def validate(self) -> None:
        """Check that all dependencies exist and there are no cycles."""
        for task in self.tasks.values():
            for dep in task.depends_on:
                if dep not in self.tasks:
                    raise ValueError(
                        f"Task '{task.task_id}' depends on unknown task '{dep}'"
                    )
        self._check_for_cycles()

    def _check_for_cycles(self) -> None:
        """Depth-first search with three colors: white/grey/black.

        Grey means "currently on the DFS path". If we ever revisit a
        grey node, we've found a cycle.
        """
        WHITE, GREY, BLACK = 0, 1, 2
        color = {task_id: WHITE for task_id in self.tasks}

        def visit(task_id: str, path: list[str]) -> None:
            color[task_id] = GREY
            path.append(task_id)
            for dep in self.tasks[task_id].depends_on:
                if color[dep] == GREY:
                    cycle = " -> ".join(path + [dep])
                    raise CycleError(f"Dependency cycle detected: {cycle}")
                if color[dep] == WHITE:
                    visit(dep, path)
            path.pop()
            color[task_id] = BLACK

        for task_id in self.tasks:
            if color[task_id] == WHITE:
                visit(task_id, [])

    def ready_tasks(self) -> list[Task]:
        """Tasks whose dependencies are all DONE and which haven't started."""
        ready = []
        for task in self.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            deps_done = all(
                self.tasks[dep].status == TaskStatus.DONE
                for dep in task.depends_on
            )
            if deps_done:
                ready.append(task)
        return ready

    def is_finished(self) -> bool:
        """True when no task can make further progress."""
        for task in self.tasks.values():
            if task.status in (TaskStatus.PENDING, TaskStatus.READY, TaskStatus.RUNNING):

                if task.status == TaskStatus.PENDING and self._is_doomed(task):
                    continue
                return False
        return True

    def _is_doomed(self, task: Task) -> bool:
        """A pending task is doomed if any upstream task FAILED or is BLOCKED."""
        return any(
            self.tasks[dep].status in (TaskStatus.FAILED, TaskStatus.BLOCKED)
            for dep in task.depends_on
        )

    def mark_failed(self, task_id: str) -> None:
        """Mark a task failed and cascade BLOCKED status downstream."""
        self.tasks[task_id].status = TaskStatus.FAILED

        changed = True
        while changed:
            changed = False
            for task in self.tasks.values():
                if task.status == TaskStatus.PENDING and self._is_doomed(task):
                    task.status = TaskStatus.BLOCKED
                    changed = True

    def summary(self) -> str:
        lines = []
        for task in self.tasks.values():
            deps = ", ".join(task.depends_on) if task.depends_on else "-"
            lines.append(
                f"  [{task.status.value:>7}] {task.task_id:<18} "
                f"role={task.role:<10} deps=({deps})"
            )
        return "\n".join(lines)
