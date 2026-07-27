
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_history: dict[str, list[float]] = {}

def check_and_record(user_id: str, max_per_window: int = 5,
                     window_seconds: int = 3600) -> tuple[bool, int]:
    now = time.time()
    with _lock:
        attempts = _history.setdefault(user_id, [])
        attempts[:] = [t for t in attempts if now - t < window_seconds]
        if len(attempts) >= max_per_window:
            return False, 0
        attempts.append(now)
        return True, max_per_window - len(attempts)
