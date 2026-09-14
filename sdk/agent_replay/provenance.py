"""Best-effort environment identifiers, never a process snapshot."""
import hashlib
import platform
import subprocess
from pathlib import Path

def collect(root='.'):
    root = Path(root).resolve()
    def git(*args):
        p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=5)
        return p.stdout.strip() if p.returncode == 0 else None
    commit = git('rev-parse', 'HEAD')
    status = git('status', '--porcelain')
    patch = git('diff', 'HEAD', '--binary') if commit else None
    patch_digest = hashlib.sha256(patch.encode()).hexdigest() if patch else None
    locks = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('uv.lock','requirements.txt','poetry.lock','pyproject.toml') if (root/name).is_file()}
    return {'sdk_version': '0.2.0', 'python_version': platform.python_version(), 'platform': platform.system(),
            'code': {'commit': commit, 'dirty': bool(status) if status is not None else None, 'source_available': commit is not None, 'dirty_diff_digest': patch_digest},
            'dependencies': {'lockfile_digests': locks}, 'capture_profile': 'explicit-python-boundaries'}
