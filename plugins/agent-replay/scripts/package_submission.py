"""Validate and create a standalone public-directory plugin ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    manifest = json.loads((ROOT / '.codex-plugin/plugin.json').read_text())
    assert re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', manifest['name'])
    assert re.fullmatch(r'\d+\.\d+\.\d+', manifest['version'])
    assert manifest['requires_local_executor'] is True
    interface = manifest['interface']
    for field, limit in [('displayName', 30), ('shortDescription', 30),
                         ('longDescription', 4000), ('developerName', 80)]:
        assert 0 < len(interface[field]) <= limit, field
    for field in ('logo', 'composerIcon'):
        relative = interface[field]
        assert relative.startswith('./')
        path = (ROOT / relative).resolve()
        assert path.is_relative_to(ROOT) and path.is_file()
        assert path.stat().st_size <= 5 * 1024 * 1024
        svg = ET.parse(path).getroot()
        assert svg.tag == '{http://www.w3.org/2000/svg}svg'
        assert float(svg.attrib['width']) == float(svg.attrib['height']) >= 48
    assert (ROOT / 'skills/agent-replay/SKILL.md').is_file()
    files = [ROOT / '.codex-plugin/plugin.json', ROOT / 'README.md',
             ROOT / 'LICENSE', ROOT / 'scripts/replay.py']
    for folder in ('skills', 'vendor', 'assets'):
        files.extend(p for p in (ROOT / folder).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts
                     and p.suffix not in ('.pyc', '.zip') and p.name != '.DS_Store')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            assert not path.is_symlink(), path
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(),
                                   date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(args.output) as archive:
        assert archive.testzip() is None
        assert '.codex-plugin/plugin.json' in archive.namelist()
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix('.zip.sha256').write_text(f'{digest}  {args.output.name}\n')
    print(f'{args.output.resolve()}\n{len(files)} files\nSHA-256: {digest}')


if __name__ == '__main__':
    main()
