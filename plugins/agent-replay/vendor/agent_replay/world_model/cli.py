import argparse
import asyncio
from pathlib import Path
import json
import sys
from ..storage import read,save
from .dataset import build,load
from .training import train,TabularWorldModel
from .evaluation import evaluate
from .refund import DeterministicRefundModel


def main(argv=None):
    p=argparse.ArgumentParser(prog='agent-replay world-model');commands=p.add_subparsers(dest='command',required=True)
    d=commands.add_parser('dataset');ds=d.add_subparsers(dest='operation',required=True);b=ds.add_parser('build');b.add_argument('traces',nargs='+');b.add_argument('--output',required=True);b.add_argument('--adapter',choices=['legacy-refund']);b.add_argument('--authorize-training',action='store_true')
    b.add_argument('--sensitive-field',action='append',default=[])
    t=commands.add_parser('train');t.add_argument('dataset');t.add_argument('--output',required=True);t.add_argument('--backend',choices=['categorical','hf-sft'],default='categorical');t.add_argument('--config')
    e=commands.add_parser('evaluate');e.add_argument('dataset');e.add_argument('--split',choices=['train','validation','test'],default='test');e.add_argument('--checkpoint');e.add_argument('--checkpoint-root');e.add_argument('--backend',choices=['deterministic','categorical','hf'],default='categorical');e.add_argument('--output',required=True)
    e.add_argument('--cost-per-million',type=float)
    r=commands.add_parser('promote');r.add_argument('--checkpoint',required=True);r.add_argument('--checkpoint-root',required=True);r.add_argument('--evaluation',required=True);r.add_argument('--baseline-evaluation',required=True);r.add_argument('--shadow-run',required=True);r.add_argument('--policy',required=True);r.add_argument('--registry',required=True)
    a=p.parse_args(argv)
    try:
        if a.command=='dataset':
            paths=[p for name in a.traces for p in (sorted(Path(name).rglob('*.json')) if Path(name).is_dir() else [Path(name)])]
            traces=[]
            for path in paths:
                value=read(path)
                if isinstance(value,dict) and value.get('schema_version') in ('1.0','2.0') and 'steps' in value:traces.append(value)
            value=build(traces,a.output,a.adapter,a.authorize_training,a.sensitive_field)
        elif a.command=='train':
            if a.backend=='hf-sft':
                from .local import train_sft
                if not a.config:raise ValueError('Local SFT config required')
                value=train_sft(a.dataset,a.output,read(a.config))
            else:value=train(a.dataset,a.output)
            save(Path(a.output).parent/(Path(a.output).name+'.reference.json'),value)
        elif a.command=='evaluate':
            _,rows=load(a.dataset,a.split)
            if a.checkpoint and a.backend=='hf':
                from .local import LocalHFWorldModel
                reference=read(a.checkpoint);model=LocalHFWorldModel(reference['checkpoints'][-1],a.checkpoint_root,price_per_million=a.cost_per_million)
            else:model=TabularWorldModel(read(a.checkpoint)['checkpoint'],a.checkpoint_root) if a.checkpoint else DeterministicRefundModel()
            value=asyncio.run(evaluate(model,rows,a.output))
        else:
            from .registry import promote
            reference=read(a.checkpoint)
            value=promote(reference.get('checkpoint') or reference['checkpoints'][-1],a.checkpoint_root,read(a.evaluation),a.shadow_run,read(a.policy),a.registry,read(a.baseline_evaluation))
        print(json.dumps(value,ensure_ascii=False));return 0
    except (ValueError,TypeError,KeyError,OSError,ImportError):
        print(json.dumps({'error':'World-model operation rejected; check authorization, schema, local dependencies and evidence'}),file=sys.stderr);return 2
