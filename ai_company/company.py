
from __future__ import annotations

from .agents import Agent, LLMBackend, MockLLM, ROLE_PROMPTS
from .conflict import FileStore
from .memory import SharedMemory, build_context
from .planner import PLANNER_INSTRUCTIONS, PlanParseError, parse_plan
from .repo import Workspace
from .scheduler import Scheduler
from .task_graph import Task, TaskGraph

class Company:
    def __init__(self, llm: LLMBackend | None = None, max_workers: int = 3,
                 verbose: bool = True):
        llm = llm or MockLLM()
        self.agents = {role: Agent(role, llm) for role in ROLE_PROMPTS}
        self.shared_memory = SharedMemory()
        self.file_store = FileStore()
        self.max_workers = max_workers
        self.verbose = verbose

    def solve_issue(self, issue: str) -> TaskGraph:
        graph = self._build_standard_workflow(issue)
        scheduler = Scheduler(
            graph=graph,
            agents=self.agents,
            shared_memory=self.shared_memory,
            file_store=self.file_store,
            issue=issue,
            max_workers=self.max_workers,
            verbose=self.verbose,
        )
        scheduler.run()
        self.last_timeline = scheduler.timeline
        return graph

    @staticmethod
    def _build_standard_workflow(issue: str) -> TaskGraph:
        graph = TaskGraph()
        graph.add_task(Task(
            task_id="spec", role="product_manager",
            title="Write requirements spec",
            description=f"Turn this issue into a spec:\n{issue}",
        ))
        graph.add_task(Task(
            task_id="plan", role="tech_lead",
            title="Write technical plan",
            description="Design the implementation based on the spec.",
            depends_on=["spec"],
        ))
        graph.add_task(Task(
            task_id="impl_backend", role="backend",
            title="Implement backend",
            description="Implement the backend portion of the plan.",
            depends_on=["plan"],
        ))
        graph.add_task(Task(
            task_id="impl_frontend", role="frontend",
            title="Implement frontend",
            description="Implement the frontend portion of the plan.",
            depends_on=["plan"],
        ))
        graph.add_task(Task(
            task_id="qa_review", role="qa",
            title="Test the implementation",
            description="Test backend + frontend against the acceptance criteria.",
            depends_on=["impl_backend", "impl_frontend"],
        ))
        graph.add_task(Task(
            task_id="security_review", role="security",
            title="Security review",
            description="Review backend + frontend for vulnerabilities.",
            depends_on=["impl_backend", "impl_frontend"],
        ))
        graph.add_task(Task(
            task_id="release", role="devops",
            title="Prepare deployment",
            description="Create build/deploy configuration once reviews pass.",
            depends_on=["qa_review", "security_review"],
        ))
        return graph

    def plan_dynamically(self, issue: str, extra_context: str = "",
                         on_step=None) -> tuple[TaskGraph, str]:
        """on_step(message), if given, is called right before each
        blocking LLM call so a caller (e.g. a web UI) can show live
        progress instead of an opaque single "planning..." line --
        useful for telling apart "still working" from "actually stuck"
        when a specific step is slow.
        """
        if on_step:
            on_step("Product Manager writing spec...")
        spec = self.agents["product_manager"].work(
            f"## Issue\n{issue}\n\nWrite the requirements spec.")
        self.shared_memory.post("product_manager", "decision", "Spec written.")

        planner_prompt = (
            f"## Issue\n{issue}\n\n## Spec\n{spec}\n\n"
            f"{extra_context}\n\n{PLANNER_INSTRUCTIONS}"
        )
        if on_step:
            on_step("Tech Lead designing task plan...")
        raw_plan = self.agents["tech_lead"].work(planner_prompt)

        try:
            graph = parse_plan(raw_plan)
            note = f"dynamic plan accepted ({len(graph.tasks)} tasks)"
        except PlanParseError as exc:
            graph = self._build_standard_workflow(issue)
            note = f"dynamic plan REJECTED ({exc}); fell back to standard workflow"

        for task in graph.tasks.values():
            task.description = f"{task.description}\n\n## Spec\n{spec}"
        return graph, note

    def solve_repo_issue(self, workspace: Workspace, issue: str,
                         test_cmd: str, max_fix_rounds: int = 2,
                         on_progress=None) -> dict:
        self.file_store = workspace.file_store
        log: list[str] = []

        def apply_edits(task, output):
            written = workspace.apply_agent_output(output, author=task.role)
            if written:
                log.append(f"{task.task_id} wrote: {', '.join(written)}")

        current_issue = f"{issue}\n\n## Repository\n{workspace.file_context()}"

        for round_no in range(1, max_fix_rounds + 2):
            graph, note = self.plan_dynamically(current_issue)
            log.append(f"round {round_no}: {note}")

            scheduler = Scheduler(
                graph=graph, agents=self.agents,
                shared_memory=self.shared_memory, file_store=self.file_store,
                issue=current_issue, max_workers=self.max_workers,
                verbose=self.verbose, on_task_done=apply_edits,
            )

            if on_progress is not None:
                original_log = scheduler._log
                def patched_log(message, _orig=original_log, _rn=round_no, _g=graph, _s=scheduler):
                    _orig(message)
                    on_progress(_rn, _g, _s)
                scheduler._log = patched_log

            scheduler.run()

            workspace.materialize()
            exit_code, test_output = workspace.run(test_cmd)
            log.append(f"round {round_no}: tests exit={exit_code}")
            if on_progress is not None:
                on_progress(round_no, graph, scheduler)

            if exit_code == 0:
                return {"resolved": True, "rounds": round_no, "log": log,
                       "test_output": test_output}

            current_issue = (
                f"{issue}\n\n## Previous attempt FAILED tests\n"
                f"```\n{test_output[-3000:]}\n```\n"
                f"Fix the code so the tests pass.\n\n"
                f"## Repository (current state)\n{workspace.file_context()}"
            )

        return {"resolved": False, "rounds": max_fix_rounds + 1, "log": log,
               "test_output": test_output}
