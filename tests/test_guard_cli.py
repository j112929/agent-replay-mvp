import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class GuardCliTests(unittest.TestCase):
    def test_demo_and_real_gate_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'sdk')}
            command = [sys.executable, '-m', 'agent_replay.cli', 'guard']
            demo = subprocess.run(command + ['demo', '--directory', directory], env=env, capture_output=True, text=True)
            self.assertEqual(demo.returncode, 0, demo.stderr)
            result = json.loads(demo.stdout)
            self.assertEqual(result['decisions'], ['PASS', 'FAIL', 'INCONCLUSIVE'])
            config = json.loads((Path(directory) / 'ci-input.json').read_text())
            args = ['run', config['case'], '--directory', config['directory'], '--baseline', config['baseline']]
            for candidate, code in ((config['candidate'], 1), (config['baseline'], 0), (None, 3)):
                run = subprocess.run(command + args + (['--candidate', candidate] if candidate else []), env=env, capture_output=True, text=True)
                self.assertEqual(run.returncode, code, run.stderr)
                output = json.loads(run.stdout); self.assertEqual(output['exit_code'], code)
                self.assertNotIn('steps', run.stdout)
            approve = subprocess.run(command + ['approve', config['case'], '--directory', config['directory']], env=env, capture_output=True, text=True)
            self.assertEqual(approve.returncode, 2)
