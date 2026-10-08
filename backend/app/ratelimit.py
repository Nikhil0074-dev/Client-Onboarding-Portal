import time
from collections import defaultdict, deque

from fastapi import HTTPException

_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > window_seconds:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait and try again.")
    q.append(now)


def reset(key: str) -> None:
    _hits.pop(key, None)
