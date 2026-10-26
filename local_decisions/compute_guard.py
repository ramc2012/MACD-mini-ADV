"""Prevent overlapping full-model training/evaluation allocations on this Mac."""
from contextlib import contextmanager
import fcntl
from pathlib import Path


@contextmanager
def exclusive_compute():
    path = Path('runtime/qwen-training/compute.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
