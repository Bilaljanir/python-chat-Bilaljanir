import threading
import time
from collections import deque

MAX_ENTRIES = 20

_entries: deque[dict] = deque(maxlen=MAX_ENTRIES)
_lock = threading.Lock()


def remember(text: str, username: str, when: float | None = None) -> None:
    """Garde un message public ; les messages privés n'entrent jamais ici."""
    at = time.time() if when is None else when
    entry = {"text": text, "username": username, "at": at}
    with _lock:
        _entries.append(entry)


def recent() -> list[dict]:
    with _lock:
        return list(_entries)

def clear() -> None:
    with _lock:
        _entries.clear()