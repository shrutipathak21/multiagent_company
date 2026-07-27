
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .conflict import FileStore, WriteConflict

SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
                     ".json", ".md", ".txt", ".yml", ".yaml", ".toml", ".cfg"}

FILE_BLOCK_RE = re.compile(
    r"```file:(?P<path>[^\n`]+)\n(?P<body>.*?)```",
    re.DOTALL,
)

FALLBACK_PATH_FENCE_RE = re.compile(
    r"```(?P<path>[\w./\\-]+\.\w+)\n(?P<body>.*?)```",
    re.DOTALL,
)

FALLBACK_COMMENT_PATH_RE = re.compile(
    r"(?:^|\n)[ \t*]*(?:#|//)?[ \t*]*(?:File|Filename)?:?[ \t*]*"
    r"(?P<path>[\w./\\-]+\.\w+)[ \t*]*\n```\w*\n(?P<body>.*?)```",
    re.DOTALL | re.IGNORECASE,
)

def extract_file_blocks(agent_output: str) -> dict[str, str]:
    """Parse ```file:path ...``` blocks out of an agent's output.

    Tries the strict, unambiguous format first. If a model deviates
    despite being instructed (common with free-tier models under load),
    falls back to two looser patterns rather than silently discarding
    the agent's actual work: a fence using the path itself as the
    language tag (```app.py), or a file path mentioned as a comment
    line directly above a normal language-tagged fence.
    """
    files: dict[str, str] = {}
    for match in FILE_BLOCK_RE.finditer(agent_output):
        path = match.group("path").strip()
        body = match.group("body")
        if not body.endswith("\n"):
            body += "\n"
        files[path] = body

    if files:
        return files

    for match in FALLBACK_PATH_FENCE_RE.finditer(agent_output):
        path = match.group("path").strip()
        body = match.group("body")
        if not body.endswith("\n"):
            body += "\n"
        files[path] = body
    if files:
        return files

    for match in FALLBACK_COMMENT_PATH_RE.finditer(agent_output):
        path = match.group("path").strip()
        body = match.group("body")
        if not body.endswith("\n"):
            body += "\n"
        files[path] = body

    return files

class Workspace:
    def __init__(self, repo_dir: str | Path, sandbox_dir: str | Path):
        self.repo_dir = Path(repo_dir)
        self.sandbox_dir = Path(sandbox_dir)
        self.file_store = FileStore()
        self._load()

    def _load(self) -> None:
        if self.sandbox_dir.exists():
            shutil.rmtree(self.sandbox_dir)
        shutil.copytree(self.repo_dir, self.sandbox_dir,
                        ignore=shutil.ignore_patterns(".git", "__pycache__",
                                                      "node_modules"))
        for path in sorted(self.sandbox_dir.rglob("*")):
            if path.is_file() and path.suffix in SOURCE_EXTENSIONS:
                rel = str(path.relative_to(self.sandbox_dir))
                content = path.read_text(encoding="utf-8", errors="replace")
                self.file_store.commit(rel, content, base_version=0, author="loader")

    def apply_agent_output(self, agent_output: str, author: str) -> list[str]:
        written = []
        for rel_path, content in extract_file_blocks(agent_output).items():
            rel_path = self._safe_rel_path(rel_path)
            _current, version = self.file_store.checkout(rel_path)
            self.file_store.commit(rel_path, content,
                                   base_version=version, author=author)
            written.append(rel_path)
        return written

    def _safe_rel_path(self, rel_path: str) -> str:
        p = Path(rel_path)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"Unsafe path from agent: {rel_path}")
        return str(p)

    def materialize(self) -> None:
        for rel_path, vf in self.file_store.files.items():
            target = self.sandbox_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(vf.content, encoding="utf-8")

    def run(self, cmd: str, timeout: int = 120) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=self.sandbox_dir,
                capture_output=True, text=True, timeout=timeout,
            )
            output = (proc.stdout + "\n" + proc.stderr).strip()
            return proc.returncode, output
        except subprocess.TimeoutExpired:
            return 124, f"TIMEOUT after {timeout}s running: {cmd}"

    def file_context(self, max_chars: int = 6000) -> str:
        parts = []
        for rel_path, vf in sorted(self.file_store.files.items()):
            parts.append(f"### {rel_path}\n```\n{vf.content}```")
        text = "\n\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (repo listing truncated)"
        return text
