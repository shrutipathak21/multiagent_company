
from __future__ import annotations

import json

from .agents import ROLE_PROMPTS
from .task_graph import Task, TaskGraph, CycleError

PLAN_MARKER = "OUTPUT_JSON_PLAN"

PLANNER_INSTRUCTIONS = f"""
{PLAN_MARKER}
Design the implementation as a task plan. Respond with ONLY a JSON
array, no prose, no markdown fences. Each element:
{{
  "task_id": "short_snake_case_id",
  "title": "one line",
  "description": "what exactly to produce",
  "role": one of {list(ROLE_PROMPTS)},
  "depends_on": ["ids", "of", "prerequisite", "tasks"]
}}
Rules: 3-10 tasks. Independent tasks should NOT depend on each other
(so they can run in parallel). Include at least one qa task that
depends on all implementation tasks.
"""

class PlanParseError(Exception):
    pass

def parse_plan(raw_output: str) -> TaskGraph:
    text = _strip_fences(raw_output)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:

        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise PlanParseError("No JSON array found in planner output")
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise PlanParseError(f"Invalid JSON after repair: {exc}") from exc

    if not isinstance(data, list) or not (1 <= len(data) <= 20):
        raise PlanParseError("Plan must be a JSON array of 1-20 tasks")

    graph = TaskGraph()
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise PlanParseError(f"Task {i} is not an object")
        try:
            role = str(item["role"])
            if role not in ROLE_PROMPTS:
                raise PlanParseError(f"Task {i} has unknown role '{role}'")
            task = Task(
                task_id=str(item["task_id"]),
                title=str(item.get("title", item["task_id"])),
                description=str(item.get("description", "")),
                role=role,
                depends_on=[str(d) for d in item.get("depends_on", [])],
            )
        except KeyError as exc:
            raise PlanParseError(f"Task {i} missing field {exc}") from exc
        try:
            graph.add_task(task)
        except ValueError as exc:
            raise PlanParseError(str(exc)) from exc

    try:
        graph.validate()
    except (ValueError, CycleError) as exc:
        raise PlanParseError(f"Invalid graph: {exc}") from exc

    return graph

def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()

        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
