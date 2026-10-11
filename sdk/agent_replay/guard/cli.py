"""Small CLI for explicit generation/review and customer-owned CI evidence."""
import argparse
import json
import sys
from pathlib import Path

from ..security.sanitizer import sanitize
from ..storage import save
from .imports import load_json, FORMATS
from .store import GuardStore
from .runner import run


def main(argv=None):
    parser = argparse.ArgumentParser(prog='agent-replay guard')
    commands = parser.add_subparsers(dest='command', required=True)
    imp = commands.add_parser('import'); imp.add_argument('file'); imp.add_argument('--format', choices=FORMATS, default='native')
    imp.add_argument('--sensitive-field', action='append', default=[])
    gen = commands.add_parser('generate'); gen.add_argument('--source', required=True); gen.add_argument('--positive'); gen.add_argument('--expected'); gen.add_argument('--case-id')
    approve = commands.add_parser('approve'); approve.add_argument('case'); approve.add_argument('--reviewer', required=True); approve.add_argument('--version', type=int)
    execute = commands.add_parser('run'); execute.add_argument('case'); execute.add_argument('--baseline'); execute.add_argument('--candidate'); execute.add_argument('--version', type=int); execute.add_argument('--output')
    show = commands.add_parser('show'); show.add_argument('case'); show.add_argument('--version', type=int)
    commands.add_parser('list'); commands.add_parser('usage'); commands.add_parser('demo')
    for command in commands.choices.values(): command.add_argument('--directory', default='.replay/guard')
    args = parser.parse_args(argv); code = 0
    try:
        store = GuardStore(args.directory, sensitive_fields=getattr(args, 'sensitive_field', ()))
        if args.command == 'import': value = store.import_trace(load_json(args.file), args.format)
        elif args.command == 'generate':
            data = store.generate(args.source, args.positive, load_json(args.expected) if args.expected else None, args.case_id)
            case = data['case']; value = {'id': case['id'], 'version': case['guard']['version'], 'review': data['review'],
                'validation': case['guard']['validation']['status'], 'reason': case['guard']['reason']}
            code = 0 if value['validation'] == 'validated' else 3
        elif args.command == 'approve': value = store.approve(args.case, args.reviewer, args.version)
        elif args.command == 'run':
            gate = run(store, args.case, args.baseline, args.candidate, args.version)
            if args.output: save(args.output, gate)
            # CI stdout contains status and references, never full trajectory bodies.
            value = {k: gate[k] for k in ('id', 'case_id', 'case_version', 'decision', 'classification', 'blocking', 'versions', 'reasons', 'exit_code')}
            value['report_dir'] = str(Path(args.directory) / 'guard/runs' / gate['id'] / 'reports')
            code = gate['exit_code']
        elif args.command == 'show': value = store.get_case(args.case, args.version)
        elif args.command == 'list':
            value = {'items': [{'id': x['case']['id'], 'title': x['case']['title'], 'version': x['case']['guard']['version'],
                               'review': x['review']['state']} for x in store.list_cases()]}
        elif args.command == 'usage': value = store.usage()
        else:
            from .demo import demo
            value = demo(args.directory)
        print(json.dumps(sanitize(value), ensure_ascii=False)); return code
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': sanitize(str(exc))}), file=sys.stderr); return 2
