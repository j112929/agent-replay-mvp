import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from agent_replay.security.access import AccessControl
from agent_replay.server import make_handler
from agent_replay.capture import Capture
from agent_replay.storage import read,save


class TenantTests(unittest.TestCase):
    def test_http_tenant_scope_role_audit_retention(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            tokens={'a':'a'*40,'b':'b'*40,'v':'v'*40}
            identities=[{'token_sha256':hashlib.sha256(tokens[k].encode()).hexdigest(),'org_id':org,'role':role,'subject':k} for k,org,role in [('a','alpha','ADMIN'),('b','beta','DEVELOPER'),('v','alpha','VIEWER')]]
            access=AccessControl(root,{'identities':identities,'retention_days':1})
            with Capture('private',root/'seed',input={}) as capture: capture.set_output('result')
            trace=read(capture.path)
            handler=make_handler(root,Path(__file__).resolve().parents[1]/'dist',{'_root':str(root)},access)
            server=ThreadingHTTPServer(('127.0.0.1',0),handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(path,key='a',body=None):
                req=Request(f'http://127.0.0.1:{server.server_port}'+path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={'Authorization':'Bearer '+tokens[key],'Content-Type':'application/json'})
                with urlopen(req) as response:return json.load(response)
            try:
                session=request('/api/v1/planning/simulate',body={'context':{'task_id':'t','task_group':'g','goal':'Deploy',
                    'state':{'tests_run':False,'compat_checked':False,'remaining_steps':6},'state_version':0,'environment_version':'deployment-memory.v1'},'config':{'enabled':True}})
                endpoint='/api/v1/planning/sessions/'+session['session_id']
                self.assertEqual(request(endpoint)['session_id'],session['session_id'])
                self.assertEqual(len(request(endpoint+'/candidates')['items']),3)
                with self.assertRaises(HTTPError) as e: request(endpoint,'b')
                self.assertEqual(e.exception.code,404);e.exception.close()
                with self.assertRaises(HTTPError) as e: request('/api/v1/planning/simulate','v',{})
                self.assertEqual(e.exception.code,403);e.exception.close()
                request('/api/v1/traces/import',body={'trace':trace,'org_id':'beta'})
                self.assertEqual(len(request('/api/v1/traces','a')['items']),1)
                self.assertEqual(request('/api/v1/traces','b')['items'],[])
                with self.assertRaises(HTTPError) as e:request('/api/v1/traces/'+trace['id'],'b')
                self.assertEqual(e.exception.code,404);e.exception.close()
                self.assertEqual(len(request('/api/v1/traces','v')['items']),1)
                for path,body in [('/api/v1/traces/import',{'trace':trace}),('/api/v1/safe-replay',{'source_id':trace['id']})]:
                    with self.assertRaises(HTTPError) as e:request(path,'v',body)
                    self.assertEqual(e.exception.code,403);e.exception.close()
                self.assertNotIn('session_token',request('/api/v1/health'))
                with self.assertRaises(HTTPError) as e:request('/api/v1/policies','b',{'release':{'environment':'DEVELOPMENT'}})
                self.assertEqual(e.exception.code,403);e.exception.close()
                request('/api/v1/policies',body={'release':{'environment':'PRODUCTION'}})
                gate=request('/api/v1/benchmark-gate',body={'baseline':'missing-a','candidate':'missing-b'})
                self.assertEqual(gate['decision'],'INCONCLUSIVE');self.assertTrue(gate['blocking'])
                self.assertEqual(len(request('/api/v1/results')['quality_gates']),1)
                self.assertEqual(request('/api/v1/results','b')['quality_gates'],[])
                for resource in ('experiments','comparisons'):
                    with self.assertRaises(HTTPError) as e:request('/api/v1/'+resource+'/00000000-0000-0000-0000-000000000000','b')
                    self.assertEqual(e.exception.code,404);e.exception.close()
                with self.assertRaises(HTTPError) as e:request('/api/traces')
                self.assertEqual(e.exception.code,403);e.exception.close()
                replay=request('/api/v1/safe-replay',body={'source_id':trace['id']})
                self.assertTrue(replay['replay']['simulated'])
                self.assertEqual(request('/api/v1/traces','b')['items'],[])
                import os
                org=root/'organizations'/'alpha'
                save(org/'reports'/'derived.json',{'trajectory_id':trace['id']})
                (org/'journals').mkdir();(org/'journals'/'one.jsonl').write_text('{}\n')
                for p in org.rglob('*'):
                    if p.is_file():os.utime(p,(1,1))
                result=request('/api/v1/retention',body={'apply':True})
                self.assertTrue(result['eligible'])
                self.assertEqual(request('/api/v1/traces')['items'],[])
                self.assertFalse(any(p.is_file() for p in org.rglob('*')))
                audit=(root/'audit'/'alpha.jsonl').read_text()
                self.assertIn('safe-replay',audit);self.assertIn('retention.apply',audit)
                for token in tokens.values():self.assertNotIn(token,audit)
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_local_store_cannot_read_nested_tenant_namespaces(self):
        from agent_replay.server import Store
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with Capture('tenant',root/'organizations'/'alpha'/'traces',input={}) as capture:
                capture.set_output('private')
            self.assertEqual(Store(root,{}).traces(),{})
            self.assertIn(capture.trace['id'],Store(root/'organizations'/'alpha',{}).traces())
