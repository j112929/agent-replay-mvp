"""Deterministic public source ZIP. dist is the canonical web source."""
from pathlib import Path
import io
import shutil
import sys
import zipfile
from sync_plugin import sync

root=Path(__file__).resolve().parents[1];check='--check' in sys.argv
assets=[p for p in (root/'dist').iterdir() if p.suffix in ('.html','.css','.js','.json')]
web=root/'sdk'/'agent_replay'/'web';web.mkdir(exist_ok=True)
for p in assets:
    target=web/p.name
    if check:
        if not target.exists() or target.read_bytes()!=p.read_bytes():raise SystemExit('Web drift: '+p.name)
    else:shutil.copyfile(p,target)
# Ship public schemas with the wheel too.
schemas=root/'sdk'/'agent_replay'/'schemas';schemas.mkdir(exist_ok=True)
for p in (root/'schema').glob('*.json'):
    target=schemas/p.name
    if check:
        if not target.exists() or target.read_bytes()!=p.read_bytes():raise SystemExit('Schema drift: '+p.name)
    else:shutil.copyfile(p,target)
sync(check)
excluded={'.git','.openai','.sites-runtime','.venv','node_modules','__pycache__','.replay','.env','.DS_Store','.vercel'}
paths=[]
for folder in ('sdk','dist','examples','tests','schema','scripts','docs','plugins','.github'):
    for p in (root/folder).rglob('*'):
        if p.is_file() and not any(part in excluded or part.endswith('.egg-info') for part in p.relative_to(root).parts) and p.suffix not in ('.pyc','.zip','.whl'):
            paths.append(p)
for name in ('README.md','LICENSE','pyproject.toml','package.json','vercel.json','.gitignore'):
    paths.append(root/name)
buffer=io.BytesIO()
with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(paths):
        info=zipfile.ZipInfo('agent-replay-mvp/'+p.relative_to(root).as_posix(),date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
        z.writestr(info,p.read_bytes())
data=buffer.getvalue();output=root/'dist'/'agent-replay-mvp.zip'
if check:
    if not output.exists() or output.read_bytes()!=data:raise SystemExit('Source ZIP drift: regenerate package')
else:output.write_bytes(data)
print(('Verified' if check else 'Packaged')+f' {len(paths)} files, {len(data):,} bytes')
