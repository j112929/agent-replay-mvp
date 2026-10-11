import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_replay.capture import Capture
from agent_replay.storage import read
from agent_replay.replay import replay_step, replay_agent, async_replay_agent
from agent_replay.security.replay import playback, ToolInterceptor, ReplayDenied, isolation_status
from agent_replay.experiments import execute


class SafeReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        with Capture('synthetic', self.root/'traces', input={}) as capture:
            for name in ('email.send', 'payment.charge', 'db.delete', 'cloud.delete', 'shell', 'file.write', 'unknown', 'http.get', 'credentials.read'):
                with capture.span(name, kind='tool', input={}, tool={'side_effect': 'unknown'}) as span:
                    span.set_output({'simulated': True})
        self.trace = read(capture.path)

    def tearDown(self): self.temp.cleanup()

    def test_playback_has_no_external_capabilities(self):
        with patch('socket.socket', side_effect=AssertionError('network')), patch('subprocess.Popen', side_effect=AssertionError('process')), patch('os.getenv', side_effect=AssertionError('credentials')):
            result = playback(self.trace)
        self.assertEqual(result['replay']['external_operations'], 0)
        self.assertEqual(len(result['replay']['blocked_operations']), 9)
        self.assertFalse(result['replay']['application_validated'])

    def test_interceptor_unknown_redacted_and_sandbox_fail_closed(self):
        interceptor = ToolInterceptor()
        with self.assertRaises(ReplayDenied): interceptor.resolve()
        step = self.trace['steps'][0]
        self.assertEqual(interceptor.resolve(step), {'simulated': True})
        for policy in ('DENY', 'ALLOW_IN_SANDBOX', 'unexpected'):
            with self.assertRaises(ReplayDenied): interceptor.resolve(step, policy)
        step['output']['replayability'] = 'redacted'
        with self.assertRaises(ReplayDenied): interceptor.resolve(step)
        self.assertFalse(isolation_status()['available'])
        for mode, kind in [('SANDBOX_REPLAY', 'TRACE_PLAYBACK'), ('MOCK_REPLAY', 'MODEL_RERUN')]:
            with self.assertRaises(ReplayDenied): playback(self.trace, mode, kind)

    def test_all_legacy_executable_routes_reject_before_callback(self):
        def dangerous(*args, **kwargs):
            (self.root/'SIDE_EFFECT').write_text('bad')
        for step in self.trace['steps']:
            with self.assertRaises(ReplayDenied): replay_step(self.trace, step['id'], mode='tool', tool_fn=dangerous)
        with self.assertRaises(ReplayDenied): replay_step(self.trace, self.trace['steps'][0]['id'], mode='live')
        with self.assertRaises(ReplayDenied): replay_agent(self.trace, dangerous)
        with self.assertRaises(ReplayDenied): asyncio.run(async_replay_agent(self.trace, dangerous))
        result = execute(self.trace, {'entrypoint_id': 'dangerous'}, {'entrypoints': {'dangerous': {'callable': 'does.not.exist:dangerous'}}}, self.root/'out')
        self.assertEqual(result['error']['type'], 'ReplayDenied')
        self.assertFalse((self.root/'SIDE_EFFECT').exists())
