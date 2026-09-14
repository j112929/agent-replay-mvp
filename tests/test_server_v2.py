import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from agent_replay.server import make_handler
from agent_replay.demo import demo
from agent_replay.storage import read
from agent_replay.project import load_project
from agent_replay.providers.anthropic import completion

class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name);story=demo(cls.root/'demo');cls.project=load_project(story['project'])
        cls.handler=make_handler(cls.root/'demo',Path(__file__).resolve().parents[1]/'dist',cls.project)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),cls.handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)
        cls.token=cls.get('health')['session_token'];cls.source=read(next((cls.root/'demo'/'traces').glob('*.json')))
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    @classmethod
    def get(cls,path):
        with urlopen(cls.url+'/api/v1/'+path) as r:return json.load(r)
    def post(self,path,body,token=True,key=None,origin=None):
        headers={'Content-Type':'application/json'}
        if token:headers['X-Replay-Token']=self.token
        if key:headers['Idempotency-Key']=key
        if origin:headers['Origin']=origin
        with urlopen(Request(self.url+'/api/v1/'+path,data=json.dumps(body).encode(),headers=headers)) as r:return json.load(r)
    def test_token_and_origin_required(self):
        for kw in ({'token':False},{'origin':'https://attacker.invalid'}):
            with self.assertRaises(HTTPError) as cm:self.post('traces/import',{'trace':self.source},**kw)
            self.assertEqual(cm.exception.code,403);cm.exception.close()
    def test_unknown_entrypoint_and_path_rejected(self):
        for spec in ({'entrypoint_id':'os:system'},{'entrypoint_id':'fixed','code_ref':'main'},{'entrypoint_id':'fixed','fixture_set':'../../secret'}):
            with self.assertRaises(HTTPError) as cm:self.post('experiments',{'source_id':self.source['id'],'spec':spec})
            self.assertEqual(cm.exception.code,400);cm.exception.close()
    def test_execute_idempotency_and_comparison(self):
        fixture=read(self.root/'demo'/'fixtures.json')['fixtures'];body={'source_id':self.source['id'],'spec':{'entrypoint_id':'fixed','fixtures':fixture}}
        job=self.post('experiments',body,key='test-run');same=self.post('experiments',body,key='test-run');self.assertEqual(job['id'],same['id'])
        deadline=time.time()+10
        while time.time()<deadline:
            job=self.get('experiments/'+job['id'])
            if job['state'] not in ('running','queued'):break
            time.sleep(.05)
        self.assertEqual(job['state'],'completed');candidate=job['result']['trace'];self.assertEqual(candidate['execution']['status'],'completed')
        report=self.post('comparisons',{'source_id':self.source['id'],'candidate_id':candidate['id']});self.assertEqual(report['coverage']['aligned'],2)
    def test_job_restart_does_not_retry(self):
        from agent_replay.server import Store
        from agent_replay.storage import save
        root=self.root/'restart';save(root/'jobs'/'one.json',{'id':'one','state':'running','idempotency_key':'key','request_digest':'digest'})
        store=Store(root,self.project);self.assertEqual(store.jobs['one']['state'],'interrupted');self.assertIn('key',store.idempotency)
    def test_new_assets_served(self):
        for name in ('dashboard.js','v2.js','dashboard.css','regression-demo.json','debugger.html','rollout.js','rollout.css','rollout-demo.json'):
            with urlopen(self.url+'/'+name) as r:self.assertEqual(r.status,200)
    def test_native_provider_transport(self):
        from http.server import BaseHTTPRequestHandler
        requests=[]
        class Mock(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                data=json.dumps({'content':[{'type':'text','text':'done'},{'type':'tool_use','id':'t1','name':'refund','input':{'amount_minor':2900}}],'stop_reason':'tool_use','usage':{'input_tokens':3,'output_tokens':2}}).encode()
                self.send_response(200);self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Mock);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            result=completion({'model':'native-model','messages':[{'role':'user','content':'refund'}],'max_tokens':10},'',f'http://127.0.0.1:{server.server_port}')
            self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['function']['name'],'refund');self.assertEqual(requests[0]['max_tokens'],10)
        finally:server.shutdown();server.server_close();thread.join()
