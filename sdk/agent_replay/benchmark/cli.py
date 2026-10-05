"""Benchmark CLI namespace, separate from existing replay and rollout commands."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import uuid
from .contracts import AgentConfig, INFRA_FAILURES
from .store import RunStore, list_runs
from .metrics import aggregate
from .compare import compare_runs, quality_gate
from .reports import write_reports


def summary_text(summary):
    d = summary['distributions']
    lines = [f"Tasks: {summary['task_count']}", f"Passed: {summary['pass_count']}",
        f"Failed: {summary['fail_count']}", f"Success rate: {summary['success_rate'] * 100:.1f}%",
        f"Median steps: {d['steps']['median']}", f"Median latency: {d['duration_ms']['median']} ms",
        f"Average tokens: {d['tokens']['mean']}", f"Average cost: {d['cost_usd']['mean']} USD",
        'Failure categories: ' + json.dumps(summary['failure_categories'])]
    return '\n'.join(lines)


def comparison_text(value):
    rate = value['metrics']['success_rate']
    if rate['delta'] is None:
        return 'No comparable tasks; task coverage/definitions differ.'
    lines = [f"baseline success: {rate['baseline']:.1%}", f"candidate success: {rate['candidate']:.1%}",
             f"delta: {rate['delta'] * 100:+.1f} pp"]
    for key in ('newly_passing', 'newly_failing', 'unchanged_pass', 'unchanged_fail', 'added_tasks', 'removed_tasks', 'changed_tasks'):
        lines.append(f"{key}: {len(value[key])} ({', '.join(value[key])})")
    for label, name, stat in (('median latency (ms)', 'duration_ms', 'median'), ('median steps', 'steps', 'median'), ('cost/task', 'cost_usd', 'mean')):
        change = value['metrics'][name][stat]
        lines.append(f"{label}: {change['baseline']} -> {change['candidate']} (delta {change['delta']})")
    for tag, metrics in value['by_tag'].items():
        rate = metrics['success_rate']
        lines.append(f"{tag}: {rate['baseline']:.1%} -> {rate['candidate']:.1%}" + (' REGRESSION' if metrics['regression'] else ''))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='agent-replay benchmark')
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('--benchmark', choices=['local'], default='local')
    run.add_argument('--suite', required=True)
    run.add_argument('--agent', choices=['demo', 'demo-v2', 'openai'], default='demo')
    run.add_argument('--model')
    run.add_argument('--base-url')
    run.add_argument('--max-model-calls', type=int, default=20)
    run.add_argument('--output')
    listing = commands.add_parser('list')
    listing.add_argument('--directory', default='.replay/benchmarks')
    show = commands.add_parser('show'); show.add_argument('run')
    compare = commands.add_parser('compare'); compare.add_argument('baseline'); compare.add_argument('candidate'); compare.add_argument('--output')
    report = commands.add_parser('report'); report.add_argument('run'); report.add_argument('--baseline'); report.add_argument('--output')
    gate = commands.add_parser('gate'); gate.add_argument('--baseline', required=True); gate.add_argument('--candidate', required=True)
    gate.add_argument('--max-success-drop', type=float, default=0)
    gate.add_argument('--max-new-failures', type=int, default=0)
    gate.add_argument('--max-cost-increase', type=float)
    for sub in (run, listing, show, compare, report, gate):
        sub.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    code = 0
    try:
        if args.command == 'run':
            from .adapters.local import LocalAdapter
            from .agents.demo import DemoAgent
            from .agents.model import OpenAICompatibleAgent
            from .runner import run_benchmark
            if not 1 <= args.max_model_calls <= 100:
                raise ValueError('--max-model-calls must be between 1 and 100')
            config = AgentConfig(args.agent, args.model, {'base_url': args.base_url, 'max_model_calls': args.max_model_calls})
            agent = OpenAICompatibleAgent(config) if args.agent == 'openai' else DemoAgent(args.agent)
            output = args.output or str(Path('.replay/benchmarks') / str(uuid.uuid4()))
            run, results = run_benchmark(LocalAdapter(args.suite), agent, config, output)
            value = {'run': asdict(run), 'output': str(Path(output).resolve()), 'reports': write_reports(output)}
            text = 'Benchmark completed\n' + summary_text(run.summary) + '\nOutput: ' + value['output']
            code = 2 if any(r.failure_category in INFRA_FAILURES for r in results) else 0
        elif args.command == 'list':
            value = list_runs(args.directory)
            text = '\n'.join(f"{r['id']} {r['benchmark']} {r['status']}" for r in value) or 'No benchmark runs'
        elif args.command == 'show':
            run, results = RunStore(args.run).load(complete=False)
            value = {'run': run, 'results': [asdict(r) for r in results], 'summary': aggregate(results)}
            text = json.dumps(value, indent=2)
        elif args.command == 'compare':
            value = compare_runs(args.baseline, args.candidate)
            if args.output:
                from ..storage import save
                save(args.output, value)
            text = comparison_text(value)
        elif args.command == 'report':
            comparison = compare_runs(args.baseline, args.run) if args.baseline else None
            value = write_reports(args.run, args.output, comparison)
            text = json.dumps(value, indent=2)
        else:
            comparison = compare_runs(args.baseline, args.candidate)
            value = quality_gate(comparison, args.max_success_drop, args.max_new_failures, args.max_cost_increase)
            code = 0 if value['passed'] else 1
            text = ('PASS' if value['passed'] else 'FAIL') + ' benchmark quality gate\n' + comparison_text(comparison)
            text += '\n' + '\n'.join(value['reasons'])
        print(json.dumps(value, ensure_ascii=False) if args.json else text)
        return code
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2
