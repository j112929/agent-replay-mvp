import argparse
import json
import sys
from pathlib import Path
from .demo import demo, records_from_run
from ..storage import read, save


def main(argv=None):
    if argv and argv[0]=='compare':
        from ..benchmark.cli import main as benchmark_main
        return benchmark_main(['compare']+list(argv[1:]))
    parser=argparse.ArgumentParser(prog='agent-replay planning')
    commands=parser.add_subparsers(dest='command',required=True)
    d=commands.add_parser('demo');d.add_argument('--output',required=True);d.add_argument('--tasks',type=int,default=20);d.add_argument('--seed',type=int,default=0)
    s=commands.add_parser('show');s.add_argument('session')
    e=commands.add_parser('dataset');e.add_argument('run');e.add_argument('--output',required=True)
    b=commands.add_parser('benchmark');b.add_argument('--output',required=True);b.add_argument('--tasks',type=int,default=20);b.add_argument('--no-ablations',action='store_true')
    r=commands.add_parser('run');r.add_argument('--config');r.add_argument('--output',required=True);r.add_argument('--tasks',type=int,default=20)
    p=commands.add_parser('replay');p.add_argument('session');p.add_argument('--config');p.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='benchmark':
            from ..world_model.experiments import run
            result=run(args.output,args.tasks,not args.no_ablations)
            value={'output':args.output,'modes':{k:v['success_rate'] for k,v in result['modes'].items()}}
        elif args.command=='run':
            from ..world_model.config import configuration
            from ..world_model.refund import RefundAdapter,RefundBaseline,DeterministicRefundModel,tasks,policy,SCHEMAS
            from .core import Planner,PlanningAgent,CandidateGenerator
            from ..benchmark.contracts import AgentConfig
            from ..benchmark.runner import run_benchmark
            if not 1<=args.tasks<=100:raise ValueError('Task bound')
            config=configuration(read(args.config) if args.config else {})
            runner=PlanningAgent(RefundBaseline(),Planner(CandidateGenerator(policy,SCHEMAS),DeterministicRefundModel(),config))
            run_,_=run_benchmark(RefundAdapter(tasks(args.tasks)),runner,AgentConfig('refund',parameters={'planning_mode':config.mode}),args.output)
            value={'output':args.output,'summary':run_.summary}
        elif args.command=='replay':
            import asyncio
            from .contracts import PlanningConfig,ImaginedTrajectory
            from .core import TrajectoryScorer
            from .store import validate_session
            session=validate_session(read(args.session));config=PlanningConfig(**read(args.config)) if args.config else PlanningConfig(**session['planning_config'])
            async def rescore():return [__import__('dataclasses').asdict(await TrajectoryScorer(config).score(ImaginedTrajectory(**r),session['origin']['goal'])) for r in session['rollouts']]
            value={'schema_version':'planning-rescore.v1','source_session_id':session['session_id'],'scores':asyncio.run(rescore()),'real_execution':False}
            save(args.output,value)
        elif args.command=='demo':
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
