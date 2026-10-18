"""Research commands; execution is restricted to bundled in-memory environments."""
import argparse
import json
from pathlib import Path
import sys
from ..storage import read, save, within


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0]=='run':
        from .experiments.runner import main as run
        return run(argv[1:])
    parser=argparse.ArgumentParser(prog='agent-replay probabilistic')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('run',help='Run a bounded synthetic research experiment')
    pub=commands.add_parser('publish');pub.add_argument('experiments',nargs='+');pub.add_argument('--output',required=True)
    replay=commands.add_parser('replay');replay.add_argument('run');replay.add_argument('task_id');replay.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='publish':
            from .experiments.reporting import public_artifact
            result=public_artifact([read(p,limit=32*1024*1024) for p in args.experiments])
        else:
            from ..benchmark.store import RunStore
            from ..security.replay import playback
            _,results=RunStore(args.run).load()
            matches=[r for r in results if r.task_id==args.task_id]
            if len(matches)!=1:raise ValueError('Unknown recorded task')
            result=playback(read(within(args.run,matches[0].trajectory_path)))
        save(args.output,result)
        print(json.dumps({'output':str(Path(args.output).resolve()),'schema_version':result.get('schema_version')}))
        return 0
    except (ValueError,OSError,KeyError,TypeError):
        print(json.dumps({'error':'Artifact rejected; use completed synthetic experiments or an existing recorded task'}),file=sys.stderr)
        return 2
