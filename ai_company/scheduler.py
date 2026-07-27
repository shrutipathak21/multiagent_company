
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, Future
import threading
import time

from .task_graph import TaskGraph, Task, TaskStatus
from .memory import SharedMemory, build_context
from .agents import Agent
from .conflict import FileStore, WriteConflict

class Scheduler:
    MAX_ATTEMPTS = 3

    def __init__(
        self,
        graph: TaskGraph,
        agents: dict[str, Agent],
        shared_memory: SharedMemory,
        file_store: FileStore,
        issue: str,
        max_workers: int = 3,
        verbose: bool = True,
        on_task_done=None,
    ):
        self.graph = graph
        self.agents = agents
        self.shared = shared_memory
        self.files = file_store
        self.issue = issue
        self.max_workers = max_workers
        self.verbose = verbose

        self.on_task_done = on_task_done
        self._lock = threading.Lock()
        self.timeline: list[str] = []

    def run(self) -> None:
        self.graph.validate()
        self._log("Scheduler started.")

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            in_flight: dict[Future, Task] = {}

            while True:

                with self._lock:
                    ready = self.graph.ready_tasks()
                    for task in ready:
                        if len(in_flight) >= self.max_workers:
                            break
                        task.status = TaskStatus.RUNNING
                        future = pool.submit(self._execute, task)
                        in_flight[future] = task
                        self._log(f"START  {task.task_id} (role={task.role})")

                if not in_flight:
                    with self._lock:
                        if self.graph.is_finished() or not self.graph.ready_tasks():
                            break
                    continue

                done_future = next(
                    f for f in self._wait_any(list(in_flight.keys()))
                )
                task = in_flight.pop(done_future)
                self._handle_completion(task, done_future)

        self._log("Scheduler finished.")

    @staticmethod
    def _wait_any(futures: list[Future]):
        from concurrent.futures import as_completed
        yield from as_completed(futures)

    def _execute(self, task: Task) -> str:
        agent = self.agents[task.role]

        with self._lock:
            dep_results = {
                dep: self.graph.tasks[dep].result or ""
                for dep in task.depends_on
            }

        context = build_context(
            issue=self.issue,
            task_description=f"{task.title}\n{task.description}",
            dependency_results=dep_results,
            shared=self.shared,
            agent_mem=agent.memory,
        )
        return agent.work(context)

    def _handle_completion(self, task: Task, future: Future) -> None:
        try:
            output = future.result()
            if self.on_task_done is not None:
                self.on_task_done(task, output)
        except WriteConflict:

            self._log(f"REBASE {task.task_id} (write conflict, retrying)")
            self._retry_or_fail(task, reason="write conflict")
            return
        except Exception as exc:
            self._log(f"ERROR  {task.task_id}: {exc}")
            self._retry_or_fail(task, reason=str(exc))
            return

        with self._lock:
            task.result = output
            task.status = TaskStatus.DONE
            self.shared.post(author=task.role, kind="decision",
                             content=f"Completed '{task.title}'.")
        self._log(f"DONE   {task.task_id}")

    def _retry_or_fail(self, task: Task, reason: str) -> None:
        with self._lock:
            task.attempts += 1
            if task.attempts < self.MAX_ATTEMPTS:
                task.status = TaskStatus.PENDING
                self._log(f"RETRY  {task.task_id} (attempt {task.attempts + 1})")
            else:
                self.graph.mark_failed(task.task_id)
                self._log(f"FAILED {task.task_id} after {task.attempts} attempts ({reason})")

    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self.timeline.append(line)
        if self.verbose:
            print(line)
