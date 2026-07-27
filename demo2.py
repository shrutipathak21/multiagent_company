
from ai_company.agents import LLMBackend
from ai_company.evals import EvalCase, run_evals, report

CORRECT_FILE = '''```file:mymath.py
"""Tiny math library."""

def add(a, b):
    return a + b

def divide(a, b):
    if b == 0:
        raise ValueError("division by zero")
    return a / b

def power(base, exp):
    return base ** exp
```'''

BUGGY_FILE = '''```file:mymath.py
"""Tiny math library."""

def add(a, b):
    return a + b

def divide(a, b):
    return a / b          # oops: forgot the divide-by-zero contract

def power(base, exp):
    return base ** exp
```'''

PLAN_JSON = (
    '[{"task_id": "impl_math", "title": "Implement divide and power",'
    ' "description": "Implement both functions per spec.",'
    ' "role": "backend", "depends_on": []},'
    ' {"task_id": "verify", "title": "QA verification",'
    ' "description": "Check acceptance criteria.",'
    ' "role": "qa", "depends_on": ["impl_math"]}]'
)

class ScriptedLLM(LLMBackend):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if "OUTPUT_JSON_PLAN" in user_prompt:
            return PLAN_JSON
        if "solo software engineer" in system_prompt.lower():
            return "Here is my fix:\n" + BUGGY_FILE
        if "backend engineer" in system_prompt.lower():
            return CORRECT_FILE
        if "product manager" in system_prompt.lower():
            return ("Acceptance criteria: 1) divide(10,4)==2.5 "
                    "2) divide by zero raises ValueError 3) power(2,10)==1024")
        if "qa" in system_prompt.lower():
            return "PASS: acceptance criteria covered."
        return "Acknowledged."

cases = [EvalCase(
    name="mymath_issue",
    repo_dir="sample_repo",
    issue="Implement divide() (must raise ValueError on b == 0) and power().",
    test_cmd="python3 -m pytest -q",
)]

results = run_evals(cases, make_llm=ScriptedLLM, sandbox_root="/tmp/eval_sandboxes")
print(report(results))
