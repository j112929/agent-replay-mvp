from agent_replay.security.replay import ReplayDenied
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from agent_replay import capture, tool, replay_step, replay_agent
from agent_replay.cli import make_handler
from agent_replay.schema import validate_trace, sanitize

class SDKTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = self.temp.name
    def tearDown(self):
        self.temp.cleanup()

    def failed_trace(self):
        @tool
        def lookup(order_id):
            raise ValueError('429 lookup failed')
        with self.assertRaises(ValueError):
            with capture('failure', self.directory) as run:
                lookup(order_id='1042')
        return run

    def test_capture_persists_on_failure_and_redacts_secrets(self):
        run = self.failed_trace()
        saved = json.loads(run.path.read_text())
        validate_trace(saved)
        self.assertEqual(saved['status'], 'error')
        self.assertEqual(saved['steps'][0]['input'], {'order_id':'1042'})
        self.assertEqual(saved['steps'][0]['error']['type'], 'ValueError')
        redacted = sanitize({'api_key':'private','nested':[{'Authorization':'Bearer abc123'}],'text':'sk-abcdefghijklmno Bearer token123'})
        self.assertNotIn('private',json.dumps(redacted))
        self.assertNotIn('abc123',json.dumps(redacted))
        self.assertNotIn('token123',json.dumps(redacted))

    def test_async_siblings_keep_order_and_parent_ids(self):
        @tool
        async def compute(value):
            await asyncio.sleep(0)
            return value*2
        async def app():
            with capture('async', self.directory) as run:
                with run.span('root'):
                    outputs=await asyncio.gather(compute(1),compute(2))
            return run,outputs
        run,outputs=asyncio.run(app())
        self.assertEqual(outputs,[2,4])
        root=run.trace['steps'][0]['id']
        self.assertEqual([s['parent_id'] for s in run.trace['steps'][1:]],[root,root])
        self.assertEqual([s['input']['value'] for s in run.trace['steps'][1:]],[1,2])

    def test_recorded_replay_and_fixture_are_isolated(self):
        original=self.failed_trace().trace
        before=copy.deepcopy(original)
        sid=original['steps'][0]['id']
        recorded=replay_step(original,sid)
        fixture=replay_step(original,sid,mode='fixture',fixture={'found':True})
        self.assertEqual(recorded['status'],'error')
        self.assertEqual(fixture['status'],'success')
        self.assertEqual(fixture['duration_ms'],0)
        self.assertIsNone(fixture['steps'][0]['error'])
        self.assertFalse(fixture['replay']['application_validated'])
        self.assertEqual(original,before)

    def test_agent_rerun_reuses_tools_and_accepts_fixture(self):
        calls=[]
        @tool
        def lookup(order_id):
            calls.append(order_id)
            raise ValueError('failure')
        def agent(run):
            return lookup(order_id='1042')['ok']
        with self.assertRaises(ValueError):
            with capture('agent',self.directory) as run:
                agent(run)
        with self.assertRaises(ReplayDenied):
            replay_agent(run.trace,agent,tool_overrides={'lookup':{'ok':True}},directory=self.directory)
        self.assertEqual(calls,['1042'])

    def test_changed_tool_inputs_fail_closed_without_live_execution(self):
        calls=[]
        @tool
        def lookup(order_id):
            calls.append(order_id)
            return {'id':order_id}
        with capture('matching',self.directory) as run:
            lookup('one')
        with self.assertRaises(ReplayDenied):
            replay_agent(run.trace,lambda _:lookup('two'),directory=self.directory)
        self.assertEqual(calls,['one'])

    def test_live_model_capture_and_replay_use_actual_transport(self):
        requests=[]
        class Provider(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path,body))
                data=json.dumps({'id':'completion-test','choices':[{'message':{'role':'assistant','content':'output from '+body['model']},'finish_reason':'stop'}],'usage':{'total_tokens':9}}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}/v1'
        try:
            with capture('model',self.directory) as run:
                run.chat(model='model-a',messages=[{'role':'user','content':'hello'}],base_url=url,api_key='')
            with self.assertRaises(ReplayDenied):
                replay_step(run.trace,run.trace['steps'][0]['id'],mode='live',model='model-b',base_url=url,api_key='')
            self.assertEqual(len(requests),1)
            self.assertEqual(requests[0][0],'/v1/chat/completions')
            self.assertEqual(run.trace['steps'][0]['model'],'model-a')
            runner=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.directory,Path(self.directory)))
            worker=threading.Thread(target=runner.serve_forever,daemon=True);worker.start()
            try:
                body=json.dumps({'trace':run.trace,'step_id':run.trace['steps'][0]['id'],'model':'model-c'}).encode()
                req=Request(f'http://127.0.0.1:{runner.server_port}/api/replay',data=body,headers={'Content-Type':'application/json'})
                with patch.dict(os.environ,{'OPENAI_BASE_URL':url,'OPENAI_API_KEY':''}):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(req)
                    self.assertEqual(error.exception.code,400)
                    error.exception.close()
                self.assertEqual(len(requests),1)
            finally:
                runner.shutdown();runner.server_close();worker.join()
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_runner_lists_traces_and_rejects_cross_origin_replay(self):
        self.failed_trace()
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.directory,Path(self.directory)))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(url+'/api/traces') as response:
                self.assertEqual(len(json.load(response)),1)
            request=Request(url+'/api/replay',data=b'{}',headers={'Origin':'https://external.example','Content-Type':'application/json'})
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code,403)
            with self.assertRaises(HTTPError):
                urlopen(url+'/../secrets.env')
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_provider_errors_are_saved_as_errors(self):
        with capture('model',self.directory) as run:
            with run.span('model',kind='llm',input={},request={'model':'x','messages':[{'role':'user','content':'hi'}]},model='x') as span:
                span.set_output({'role':'assistant','content':'old'})
        with patch('agent_replay.replay.completion',side_effect=RuntimeError('provider unavailable')):
            with self.assertRaises(ReplayDenied):
                replay_step(run.trace,run.trace['steps'][0]['id'],mode='live',model='y')
        self.assertEqual(run.trace['steps'][0]['status'],'success')

class InterruptedCaptureTests(unittest.TestCase):
    def test_unwound_capture_finalizes_pending_child_and_late_cleanup(self):
        from agent_replay.capture import Capture
        from agent_replay.storage import read
        with tempfile.TemporaryDirectory() as tmp:
            run=Capture('interrupted child',tmp,input={},durability='sync')
            with self.assertRaises(TimeoutError):
                with run:
                    pending=run.span('cancelled child',input={})
                    pending.__enter__()
                    raise TimeoutError('Synthetic deadline')
            trace=read(run.path)
            self.assertEqual(trace['steps'][0]['execution']['status'],'error')
            self.assertEqual(trace['capture_health']['state'],'partial')
            self.assertEqual(replay_step(trace,trace['steps'][0]['id'])['status'],'error')
            self.assertFalse(pending.__exit__(None,None,None))
            with Capture('next capture',tmp,input={},durability='sync') as following:
                self.assertIsNone(following._current_event)

if __name__=='__main__':
    unittest.main()
