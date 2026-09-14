"""Detached code experiments with explicit, prepared interpreters."""
import contextlib
import subprocess
import tempfile
from pathlib import Path

@contextlib.contextmanager
def checkout(project,ref):
    root=Path(project['_root'])
    sha=subprocess.check_output(['git','-C',str(root),'rev-parse','--verify',ref+'^{commit}'],text=True).strip()
    with tempfile.TemporaryDirectory(prefix='replay-code-') as temp:
        target=Path(temp)/'checkout'
        subprocess.run(['git','-C',str(root),'worktree','add','--detach',str(target),sha],check=True,capture_output=True)
        try:
            configured=dict(project);configured['_root']=str(target);configured['_commit']=sha
            yield configured
        finally:
            subprocess.run(['git','-C',str(root),'worktree','remove','--force',str(target)],check=True,capture_output=True)
