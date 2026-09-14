"""v2 command dispatcher; legacy replay/serve remain compatible."""
import argparse
import json
from pathlib import Path
from .storage import read,save
from .schema import validate_trace
from .project import load_project
from .migrations import to_v2
from .experiments import execute,resolve_spec
from .comparison import compare
from .bundle import export_bundle,import_bundle
from .journal import recover
from .runtime.fidelity import doctor
from .regression import cases
from .regression.runner import run_suite

COMMANDS={'inspect','doctor','bundle','recover','run','experiment','compare','case','test','demo','cleanup'}

def main(argv):
    p=argparse.ArgumentParser(prog='agent-replay');commands=p.add_subparsers(dest='command',required=True)
    for name in ('inspect','doctor','run','experiment'):
        c=commands.add_parser(name);c.add_argument('trace');c.add_argument('--json',action='store_true')
        if name=='inspect':c.add_argument('--step')
        else:c.add_argument('--project',default='agent-replay.json');c.add_argument('--entrypoint')
        if name in ('run','experiment'):
            c.add_argument('--spec');c.add_argument('--policy',default='frozen');c.add_argument('--commit');c.add_argument('--directory',default='.replay')
        if name=='experiment':c.add_argument('--variants',required=True)
    c=commands.add_parser('compare');c.add_argument('original');c.add_argument('candidate');c.add_argument('--output');c.add_argument('--json',action='store_true')
    c=commands.add_parser('recover');c.add_argument('journal');c.add_argument('--output')
    c=commands.add_parser('bundle');c.add_argument('action',choices=['export','import']);c.add_argument('source');c.add_argument('--output');c.add_argument('--directory',default='.replay')
    c=commands.add_parser('test');c.add_argument('suite');c.add_argument('--project',default='agent-replay.json');c.add_argument('--report-dir',default='.replay/reports');c.add_argument('--live',action='store_true');c.add_argument('--json',action='store_true')
    c=commands.add_parser('case');c.add_argument('action',choices=['create','accept']);c.add_argument('source');c.add_argument('--entrypoint');c.add_argument('--output');c.add_argument('--result')
    c=commands.add_parser('cleanup');c.add_argument('--directory',default='.replay');c.add_argument('--project',default='agent-replay.json');c.add_argument('--days',type=int,default=30);c.add_argument('--apply',action='store_true')
    c=commands.add_parser('demo');c.add_argument('--directory',default='.replay/demo')
    args=p.parse_args(argv);code=0
    try:
        if args.command=='cleanup':
            from .retention import cleanup
            value=cleanup(args.directory,load_project(args.project)['_root'],args.days,args.apply)
        elif args.command=='inspect':
            t=to_v2(validate_trace(read(args.trace)))
            value=next((s for s in t['steps'] if s['id']==args.step),None) if args.step else t
            if value is None:raise ValueError('Unknown step ID')
        elif args.command=='recover':value=recover(args.journal,args.output)
        elif args.command=='bundle':
            if args.action=='export' and not args.output:raise ValueError('--output is required')
            value=export_bundle(args.source,args.output) if args.action=='export' else import_bundle(args.source,args.directory)
        elif args.command=='compare':
            value=compare(validate_trace(read(args.original)),validate_trace(read(args.candidate)))
            if args.output:save(args.output,value)
        elif args.command=='case':
            if args.action=='create':
                if not args.entrypoint or not args.output:raise ValueError('--entrypoint and --output are required')
                value=cases.create(args.source,args.entrypoint,args.output)
            else:
                if not args.result:raise ValueError('--result is required')
                value=cases.accept(args.source,args.result)
        elif args.command=='test':
            path=Path(args.suite);paths=[path] if path.is_file() else sorted(path.rglob('*.case.json'))
            value=run_suite(paths,load_project(args.project),args.report_dir,allow_live=args.live);code=value['exit_code']
        elif args.command=='demo':
            from .demo import demo
            value=demo(args.directory)
        else:
            trace=validate_trace(read(args.trace));project=load_project(args.project)
            if args.command=='doctor':value=doctor(trace,project,args.entrypoint);code=0 if value['ready'] else 3
            else:
                spec=read(args.spec) if args.spec else {'scope':'agent','policy':args.policy}
                if args.entrypoint:
                    if spec.get('entrypoint_id') and spec['entrypoint_id']!=args.entrypoint:raise ValueError('Conflicting entrypoints')
                    spec['entrypoint_id']=args.entrypoint
                if args.commit:spec['code_ref']=args.commit
                if args.command=='run':
                    spec=resolve_spec(spec,project,Path(args.spec).parent if args.spec else None)
                    value=execute(trace,spec,project,args.directory)
                    error=value.get('trace',{}).get('execution',{}).get('error',{})
                    code=2 if value['state']!='completed' else 3 if error.get('type') in ('ReplayMismatch','RecordedError') else 1 if error else 0
                else:
                    variants=read(args.variants)
                    if not isinstance(variants,list) or not 1<=len(variants)<=20:raise ValueError('Supply 1–20 explicit variants')
                    value={'experiments':[execute(trace,resolve_spec({**spec,**v},project,Path(args.variants).parent),project,args.directory) for v in variants]}
                    code=2 if any(x['state']!='completed' for x in value['experiments']) else 0
        print(json.dumps(value,ensure_ascii=False));return code
    except (ValueError,OSError,KeyError,TypeError) as exc:
        import sys
        print(json.dumps({'error':str(exc)}),file=sys.stderr);return 2
