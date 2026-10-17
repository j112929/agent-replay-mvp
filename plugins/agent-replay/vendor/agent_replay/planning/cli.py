import argparse
import json
import sys
from pathlib import Path
from .demo import demo, records_from_run
from ..storage import read, save


def main(argv=None):
    parser=argparse.ArgumentParser(prog='agent-replay planning')
    commands=parser.add_subparsers(dest='command',required=True)
    d=commands.add_parser('demo');d.add_argument('--output',required=True);d.add_argument('--tasks',type=int,default=20);d.add_argument('--seed',type=int,default=0)
    s=commands.add_parser('show');s.add_argument('session')
    e=commands.add_parser('dataset');e.add_argument('run');e.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='demo':
            if not 4<=args.tasks<=100: raise ValueError('Use 4–100 demo tasks')
            result=demo(args.output,args.tasks,args.seed)
            value={'output':str(Path(args.output).resolve()),'modes':{m:{k:v[k] for k in ('success_rate','passed','failed')} for m,v in result['modes'].items()},
                'prediction_comparison':result['offline_prediction_comparison']['decision'],'quality_gate':result['quality_gate']['decision'],'limitations':result['limitations']}
        elif args.command=='dataset':
            values=records_from_run(args.run);save(args.output,values);value={'transitions':len(values),'output':args.output}
        else:
            from .store import validate_session
            value=validate_session(read(args.session))
        print(json.dumps(value,ensure_ascii=False));return 0
    except (ValueError,OSError,KeyError,TypeError):
        print(json.dumps({'error':'Planning command rejected; inspect schema, budgets, evidence and output directory'}),file=sys.stderr);return 2
