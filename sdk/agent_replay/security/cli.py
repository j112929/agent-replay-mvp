import argparse
import json
import sys
from ..storage import read, save
from .replay import playback


def replay_main(argv):
    parser = argparse.ArgumentParser(prog='agent-replay safe-replay')
    parser.add_argument('trace')
    parser.add_argument('--mode', choices=['MOCK_REPLAY', 'SANDBOX_REPLAY'], default='MOCK_REPLAY')
    parser.add_argument('--kind', choices=['TRACE_PLAYBACK', 'MODEL_RERUN', 'SANDBOX_EXECUTION'], default='TRACE_PLAYBACK')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        result = playback(read(args.trace), args.mode, args.kind)
        save(args.output, result)
        print(json.dumps({'path': args.output, 'replay': result['replay'], 'security': result['security']}))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2


def demo_main(argv):
    from .demo import demo
    parser=argparse.ArgumentParser(prog='agent-replay security-demo')
    parser.add_argument('--directory',default='.replay/security-demo')
    args=parser.parse_args(argv)
    try:
        print(json.dumps(demo(args.directory)))
        return 0
    except (ValueError,OSError) as exc:
        print(json.dumps({'error':str(exc)}),file=sys.stderr)
        return 2
