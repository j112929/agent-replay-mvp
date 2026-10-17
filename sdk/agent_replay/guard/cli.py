"""Small CLI for explicit generation/review and customer-owned CI evidence."""
import argparse
import json
import os
import sys
from pathlib import Path

from ..security.sanitizer import sanitize
from ..storage import save
from ..serialization import digest
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
    challenge = commands.add_parser('challenge')
    challenge.add_argument('case'); challenge.add_argument('--baseline', required=True)
    challenge.add_argument('--commit', required=True); challenge.add_argument('--candidate-version', required=True)
    challenge.add_argument('--configuration', required=True); challenge.add_argument('--runner', required=True)
    challenge.add_argument('--max-cost-usd', type=float, required=True)
    challenge.add_argument('--ttl', type=int, default=900); challenge.add_argument('--version', type=int)
    challenge.add_argument('--output', required=True)
    signed = commands.add_parser('attest')
    signed.add_argument('--challenge', required=True); signed.add_argument('--trace', required=True)
    signed.add_argument('--configuration', required=True); signed.add_argument('--repository', required=True)
    signed.add_argument('--run-id', required=True); signed.add_argument('--cost-usd', type=float, required=True)
    signed.add_argument('--output', required=True)
    release = commands.add_parser('release')
    release.add_argument('case'); release.add_argument('--baseline', required=True)
    release.add_argument('--candidate', required=True); release.add_argument('--evidence', required=True)
    release.add_argument('--version', type=int); release.add_argument('--output')
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
        elif args.command == 'challenge':
            from .evidence import issue
            value = issue(store, args.case, args.baseline, args.commit, args.candidate_version,
                load_json(args.configuration), args.runner, args.max_cost_usd, args.ttl, args.version)
            save(args.output, value)
        elif args.command == 'attest':
            from .evidence import attest
            bundle = attest(load_json(args.challenge), load_json(args.trace), os.environ.get('GUARD_EVIDENCE_SIGNING_KEY'),
                args.repository, args.run_id, load_json(args.configuration), args.cost_usd)
            save(args.output, bundle)
            value = {'manifest_digest': digest(bundle['manifest']),
                     'trace_id': bundle['manifest']['trace_id'], 'output': str(Path(args.output).resolve())}
        elif args.command in ('run', 'release'):
            strict = args.command == 'release'
            gate = run(store, args.case, args.baseline, args.candidate, args.version,
                require_evidence=strict, release_evidence=load_json(args.evidence) if strict else None,
                evidence_key=os.environ.get('GUARD_EVIDENCE_SIGNING_KEY') if strict else None)
            if args.output: save(args.output, gate)
            # CI stdout contains status and references, never full trajectory bodies.
            value = {k: gate[k] for k in ('id', 'case_id', 'case_version', 'decision', 'classification', 'blocking', 'versions', 'reasons', 'exit_code', 'release_eligible')}
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
