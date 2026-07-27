
from __future__ import annotations

from dataclasses import dataclass
import difflib

@dataclass
class VersionedFile:
    filename: str
    content: str
    version: int = 0

class WriteConflict(Exception):

    def __init__(self, filename: str, latest_content: str, latest_version: int):
        super().__init__(f"Write conflict on {filename}")
        self.filename = filename
        self.latest_content = latest_content
        self.latest_version = latest_version

class FileStore:

    def __init__(self) -> None:
        self.files: dict[str, VersionedFile] = {}

        self.history: dict[tuple[str, int], str] = {}
        self.conflict_log: list[str] = []

    def checkout(self, filename: str) -> tuple[str, int]:
        vf = self.files.get(filename)
        if vf is None:
            return "", 0
        return vf.content, vf.version

    def commit(self, filename: str, new_content: str, base_version: int, author: str) -> int:
        vf = self.files.get(filename)

        if vf is None:
            self.files[filename] = VersionedFile(filename, new_content, version=1)
            self.history[(filename, 1)] = new_content
            return 1

        if vf.version == base_version:
            vf.content = new_content
            vf.version += 1
            self.history[(filename, vf.version)] = new_content
            return vf.version

        base_content = self.history.get((filename, base_version), "")
        merged = three_way_merge(base_content, vf.content, new_content)

        if merged is not None:
            self.conflict_log.append(
                f"AUTO-MERGED {filename}: {author}'s edit merged with v{vf.version}"
            )
            vf.content = merged
            vf.version += 1
            self.history[(filename, vf.version)] = merged
            return vf.version

        self.conflict_log.append(
            f"CONFLICT on {filename}: {author} based on v{base_version}, "
            f"but file is at v{vf.version}. Task must rebase."
        )
        raise WriteConflict(filename, vf.content, vf.version)

def three_way_merge(base: str, theirs: str, ours: str) -> str | None:
    base_lines = base.splitlines()
    their_lines = theirs.splitlines()
    our_lines = ours.splitlines()

    their_ops = _diff_opcodes(base_lines, their_lines)
    our_ops = _diff_opcodes(base_lines, our_lines)

    their_touched = _touched_ranges(their_ops)
    our_touched = _touched_ranges(our_ops)

    for t_start, t_end in their_touched:
        for o_start, o_end in our_touched:
            if t_start < o_end and o_start < t_end:
                return None

    all_ops = [op for op in their_ops + our_ops if op[0] != "equal"]
    all_ops.sort(key=lambda op: op[1], reverse=True)

    merged = list(base_lines)
    for tag, i1, i2, new_segment in all_ops:
        merged[i1:i2] = new_segment
    return "\n".join(merged) + ("\n" if (theirs.endswith("\n") or ours.endswith("\n")) else "")

def _diff_opcodes(base_lines: list[str], new_lines: list[str]):
    sm = difflib.SequenceMatcher(a=base_lines, b=new_lines, autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        ops.append((tag, i1, i2, new_lines[j1:j2]))
    return ops

def _touched_ranges(ops) -> list[tuple[int, int]]:
    ranges = []
    for tag, i1, i2, _segment in ops:
        if tag == "equal":
            continue
        ranges.append((i1, max(i2, i1 + 1)))
    return ranges
