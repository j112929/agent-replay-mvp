"""Copy the one SDK source into its self-contained plugin distribution."""
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]

def sync(check=False):
    source=ROOT/'sdk'/'agent_replay';target=ROOT/'plugins'/'agent-replay'/'vendor'/'agent_replay'
    paths=[p for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.zip')]
    expected={p.relative_to(source) for p in paths}
    actual={p.relative_to(target) for p in target.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    drift=[]
    for p in paths:
        dest=target/p.relative_to(source)
        if not dest.exists() or dest.read_bytes()!=p.read_bytes():
            drift.append(str(dest.relative_to(ROOT)))
            if not check:dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    for rel in actual-expected:
        drift.append(str(target/rel))
        if not check:(target/rel).unlink()
    if check and drift:raise SystemExit('Plugin drift: '+', '.join(drift))

if __name__=='__main__':sync('--check' in sys.argv)
