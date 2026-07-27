
from __future__ import annotations

from dataclasses import dataclass, field
import time

@dataclass
class MemoryEntry:
    author: str
    kind: str
    content: str
    timestamp: float = field(default_factory=time.time)

class SharedMemory:

    def __init__(self) -> None:
        self.entries: list[MemoryEntry] = []
        self.artifacts: dict[str, str] = {}

    def post(self, author: str, kind: str, content: str) -> None:
        self.entries.append(MemoryEntry(author=author, kind=kind, content=content))

    def save_artifact(self, filename: str, content: str) -> None:
        self.artifacts[filename] = content

    def recent(self, n: int = 5) -> list[MemoryEntry]:
        return self.entries[-n:]

class AgentMemory:

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self.notes: list[str] = []

    def remember(self, note: str) -> None:
        self.notes.append(note)

    def recall(self, n: int = 3) -> list[str]:
        return self.notes[-n:]

def build_context(
    issue: str,
    task_description: str,
    dependency_results: dict[str, str],
    shared: SharedMemory,
    agent_mem: AgentMemory,
    max_recent_decisions: int = 5,
) -> str:
    parts = [f"## Original issue\n{issue}", f"## Your current task\n{task_description}"]

    if dependency_results:
        dep_text = "\n\n".join(
            f"### Output of '{task_id}'\n{result}"
            for task_id, result in dependency_results.items()
        )
        parts.append(f"## Work you are building on\n{dep_text}")

    decisions = [e for e in shared.recent(max_recent_decisions) if e.kind == "decision"]
    if decisions:
        dec_text = "\n".join(f"- [{e.author}] {e.content}" for e in decisions)
        parts.append(f"## Recent team decisions\n{dec_text}")

    own_notes = agent_mem.recall()
    if own_notes:
        notes_text = "\n".join(f"- {n}" for n in own_notes)
        parts.append(f"## Your own earlier notes\n{notes_text}")

    return "\n\n".join(parts)
