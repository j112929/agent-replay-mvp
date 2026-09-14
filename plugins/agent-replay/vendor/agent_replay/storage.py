"""Atomic, content-checked local artifacts."""
import json
import os
from pathlib import Path
import tempfile
from .serialization import canonical

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(canonical(value) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return path

def read(path, limit=5*1024*1024):
    p = Path(path)
    if p.stat().st_size > limit:
        raise ValueError('Artifact exceeds size limit')
    return json.loads(p.read_text())

def within(root, path):
    root = Path(root).resolve()
    result = (root / path).resolve()
    if not result.is_relative_to(root):
        raise ValueError('Path escapes project root')
    return result
