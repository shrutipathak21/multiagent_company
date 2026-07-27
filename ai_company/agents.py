
from __future__ import annotations

import os

from .memory import AgentMemory

FILE_FORMAT_INSTRUCTIONS = (
    "\n\nWhen you write or change a file, output it EXACTLY in this format "
    "(this exact fence syntax is required, not standard markdown code "
    "fences -- your code will not be saved otherwise):\n\n"
    "```file:path/to/file.py\n"
    "<the complete file content goes here>\n"
    "```\n\n"
    "Example -- if asked to add a function to app.py, output:\n\n"
    "```file:app.py\n"
    "def add(a, b):\n"
    "    return a + b\n"
    "```\n\n"
    "Rules: the path immediately follows `​```file:` on the same line, with "
    "no space. Use the file's full path relative to the repo root, exactly "
    "matching an existing path when editing an existing file. One fence per "
    "file. Do not use plain ```python or ```javascript fences for files you "
    "want saved -- only the ```file:path format is extracted and written."
)

ROLE_PROMPTS: dict[str, str] = {
    "product_manager": (
        "You are the Product Manager of a small software company. "
        "Given a GitHub issue, produce a short, concrete requirements spec: "
        "user story, acceptance criteria (numbered), and what is OUT of scope."
    ),
    "tech_lead": (
        "You are the Tech Lead. Given a requirements spec, produce a technical "
        "plan: file-level design, interfaces between components, and risks. "
        "Be specific about file names and function signatures."
    ),
    "backend": (
        "You are a Backend Engineer. Implement exactly what the technical plan "
        "assigns to the backend. Output complete, runnable code files."
        + FILE_FORMAT_INSTRUCTIONS
    ),
    "frontend": (
        "You are a Frontend Engineer. Implement exactly what the technical plan "
        "assigns to the frontend. Output complete, runnable code files."
        + FILE_FORMAT_INSTRUCTIONS
    ),
    "qa": (
        "You are a QA Engineer. Given implemented code, write test cases as a "
        "complete, runnable test file, identify bugs and edge cases, and give "
        "a clear PASS/FAIL verdict with reasons."
        + FILE_FORMAT_INSTRUCTIONS
    ),
    "devops": (
        "You are a DevOps Engineer. Produce the build/run configuration "
        "(Dockerfile, CI steps) for the implemented code. Keep it minimal."
        + FILE_FORMAT_INSTRUCTIONS
    ),
    "security": (
        "You are a Security Engineer. Review the implemented code for "
        "vulnerabilities (injection, auth issues, secrets in code, unsafe "
        "deserialization). List findings by severity. If you recommend a "
        "code fix, output the corrected file using the same file format as "
        "other engineers."
        + FILE_FORMAT_INSTRUCTIONS
    ),
}


class LLMBackend:

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

class MockLLM(LLMBackend):

    def complete(self, system_prompt: str, user_prompt: str) -> str:

        if "OUTPUT_JSON_PLAN" in user_prompt:
            return (
                '[\n'
                ' {"task_id": "impl", "title": "Implement fix", '
                '"description": "Implement the change.", "role": "backend", "depends_on": []},\n'
                ' {"task_id": "review", "title": "QA review", '
                '"description": "Verify the change.", "role": "qa", "depends_on": ["impl"]}\n'
                ']'
            )
        role = self._guess_role(system_prompt)
        first_line = user_prompt.strip().splitlines()[0] if user_prompt.strip() else ""
        return (
            f"[MOCK {role} OUTPUT]\n"
            f"Responding to: {first_line[:80]}\n"
            f"- Deliverable produced for role '{role}'.\n"
            f"- (Plug in AnthropicLLM for real content.)"
        )

    @staticmethod
    def _guess_role(system_prompt: str) -> str:
        for role in ROLE_PROMPTS:
            keyword = role.replace("_", " ")
            if keyword in system_prompt.lower():
                return role
        return "generic"

class AnthropicLLM(LLMBackend):

    def __init__(self, model: str = "claude-sonnet-4-6", max_tokens: int = 2000):
        import anthropic
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

class Agent:
    def __init__(self, role: str, llm: LLMBackend):
        if role not in ROLE_PROMPTS:
            raise ValueError(f"Unknown role '{role}'. Known: {list(ROLE_PROMPTS)}")
        self.role = role
        self.system_prompt = ROLE_PROMPTS[role]
        self.llm = llm
        self.memory = AgentMemory(agent_name=role)

    def work(self, context: str) -> str:
        output = self.llm.complete(self.system_prompt, context)
        self.memory.remember(f"Produced output starting with: {output[:60]}")
        return output
