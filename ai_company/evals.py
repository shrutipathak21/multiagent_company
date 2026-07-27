
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from .agents import Agent, LLMBackend
from .company import Company
from .repo import Workspace

SINGLE_AGENT_PROMPT = (
    "You are a solo software engineer. Solve the issue completely in one "
    "response. Output every changed file as a fenced block:\n"
    "```file:path/to/file.py\n<full content>\n```"
)

@dataclass
class EvalCase:
    name: str
    repo_dir: str
    issue: str
    test_cmd: str

@dataclass
class EvalResult:
    case: str
    mode: str
    resolved: bool
    llm_calls: int
    seconds: float

class CountingLLM(LLMBackend):

    def __init__(self, inner: LLMBackend):
        self.inner = inner
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return self.inner.complete(system_prompt, user_prompt)

def run_single_baseline(case: EvalCase, llm: CountingLLM, sandbox: Path) -> bool:
    ws = Workspace(case.repo_dir, sandbox)
    prompt = (f"## Issue\n{case.issue}\n\n## Repository\n{ws.file_context()}")
    output = llm.complete(SINGLE_AGENT_PROMPT, prompt)
    ws.apply_agent_output(output, author="solo")
    ws.materialize()
    exit_code, _ = ws.run(case.test_cmd)
    return exit_code == 0

def run_company(case: EvalCase, llm: CountingLLM, sandbox: Path) -> bool:
    ws = Workspace(case.repo_dir, sandbox)
    company = Company(llm=llm, verbose=False)
    result = company.solve_repo_issue(ws, case.issue, case.test_cmd)
    return result["resolved"]

def run_evals(cases: list[EvalCase], make_llm, sandbox_root: str) -> list[EvalResult]:
    results: list[EvalResult] = []
    root = Path(sandbox_root)

    for case in cases:
        for mode, runner in [("single", run_single_baseline), ("company", run_company)]:
            llm = CountingLLM(make_llm())
            sandbox = root / f"{case.name}_{mode}"
            start = time.time()
            try:
                resolved = runner(case, llm, sandbox)
            except Exception:
                resolved = False
            results.append(EvalResult(
                case=case.name, mode=mode, resolved=resolved,
                llm_calls=llm.calls, seconds=round(time.time() - start, 2),
            ))
    return results

def report(results: list[EvalResult]) -> str:
    lines = [
        f"{'case':<20} {'mode':<9} {'resolved':<9} {'llm_calls':<10} {'seconds':<8}",
        "-" * 60,
    ]
    for r in results:
        lines.append(f"{r.case:<20} {r.mode:<9} {str(r.resolved):<9} "
                     f"{r.llm_calls:<10} {r.seconds:<8}")

    for mode in ("single", "company"):
        subset = [r for r in results if r.mode == mode]
        if subset:
            rate = sum(r.resolved for r in subset) / len(subset)
            calls = sum(r.llm_calls for r in subset)
            lines.append(f"\n{mode}: resolve rate {rate:.0%} "
                         f"({sum(r.resolved for r in subset)}/{len(subset)}), "
                         f"total llm_calls {calls}")
    return "\n".join(lines)
