import threading
import time
from collections import deque

MAX_ENTRIES = 20

_entries: deque[dict] = deque(maxlen=MAX_ENTRIES)
_lock = threading.Lock()


def remember(text: str, username: str) -> None:
    """Garde un message public ; les messages privés n'entrent jamais ici."""
    entry = {"text": text, "username": username, "at": time.time()}
    with _lock:
        _entries.append(entry)


def recent() -> list[dict]:
    with _lock:
        return list(_entries)
