"""Same-origin loopback service with registered entrypoints and persisted jobs."""
import hashlib
import json
import secrets
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from .storage import read,save,within
from .schema import validate_trace
from .migrations import to_v2
from .serialization import digest
from .experiments import execute,resolve_spec
from .runtime.fidelity import doctor
from .comparison import compare
from .regression import cases
from .regression.runner import run_suite

class Store:
    def __init__(self,directory,project):
        self.directory=Path(directory).resolve();self.project=project;self.token=secrets.token_urlsafe(32);self.jobs={};self.lock=threading.Lock();self.cancel={};self.idempotency={}
        for p in (self.directory/'jobs').glob('*.json'):
            job=read(p)
            if job['state'] in ('running','queued'):job['state']='interrupted';save(p,job)
            self.jobs[job['id']]=job
            if job.get('idempotency_key'):self.idempotency[job['idempotency_key']]=(job['request_digest'],job['id'])
    def traces(self):
        found={}
        for p in self.directory.rglob('*.json'):
            try:
                t=read(p)
                if isinstance(t,dict) and t.get('schema_version') in ('1.0','2.0') and 'steps' in t:
                    t=to_v2(validate_trace(t));found[t['id']]=t
            except (ValueError,OSError,KeyError,TypeError):continue
        return found
    def start(self,body,action,key=None):
        signature=digest(body)
        with self.lock:
            if key and key in self.idempotency:
                old,ident=self.idempotency[key]
                if old!=signature:raise ValueError('Idempotency key conflicts with previous request')
                return self.jobs[ident]
            if any(j['state'] in ('running','queued') for j in self.jobs.values()):raise RuntimeError('Runner is busy')
            ident=str(uuid.uuid4());job={'id':ident,'state':'queued','idempotency_key':key,'request_digest':signature};self.jobs[ident]=job;event=threading.Event();self.cancel[ident]=event
            if key:self.idempotency[key]=(signature,ident)
            save(self.directory/'jobs'/(ident+'.json'),job)
        def work():
            job['state']='running';save(self.directory/'jobs'/(ident+'.json'),job)
            try:
                result=action(event);job.update(state='completed',result=result)
                if result.get('state') in ('cancelled','failed','interrupted'):job['state']=result['state']
            except Exception as exc:job.update(state='failed',error=str(exc))
            finally:save(self.directory/'jobs'/(ident+'.json'),job)
        threading.Thread(target=work,daemon=True).start();return job

def make_handler(directory,web_root,project):
    from .cli import make_handler as legacy_handler
    Base=legacy_handler(str(Path(directory)/'traces'),web_root);store=Store(directory,project)
    class Handler(Base):
        def do_GET(self):
            if not self.local_request():return
            path=urlsplit(self.path).path
            if path=='/api/v1/health':return self.send_json({'service':'agent-replay','version':'0.2.0','schema_versions':['1.0','2.0'],'session_token':store.token,'capabilities':['inspect','run','compare','case','suite'],'entrypoints':list(project.get('entrypoints',{})),'suites':list(project.get('suites',{}))+['created-cases']})
            if path=='/api/v1/traces':
                query=parse_qs(urlsplit(self.path).query)
                try:offset=max(0,int(query.get('cursor',['0'])[0]));limit=min(100,max(1,int(query.get('limit',['100'])[0])))
                except ValueError:return self.send_json({'error':{'code':'invalid_query','message':'Invalid cursor'}},400)
                values=list(store.traces().values());page=values[offset:offset+limit]
                return self.send_json({'items':page,'next_cursor':str(offset+limit) if offset+limit<len(values) else None})
            if path=='/api/v1/failures':return self.send_json({'items':[t for t in store.traces().values() if t['execution']['status']=='error' or t.get('failures')]})
            if path.startswith('/api/v1/traces/'):
                t=store.traces().get(path.rsplit('/',1)[1]);return self.send_json(t if t else {'error':{'code':'not_found','message':'Unknown trace'}},200 if t else 404)
            if path.startswith('/api/v1/experiments/') or path.startswith('/api/v1/suites/'):
                job=store.jobs.get(path.rsplit('/',1)[1]);return self.send_json(job or {'error':{'code':'not_found','message':'Unknown job'}},200 if job else 404)
            if path=='/api/v1/results':
                values=[]
                for p in (store.directory/'reports').glob('*.json'):
                    try:
                        value=read(p)
                        if value.get('case_id') and value.get('verdict'): values.append(value)
                    except (ValueError,OSError,AttributeError): pass
                return self.send_json({'items':values})
            if path=='/api/v1/cases':
                values=[]
                for p in (Path(project['_root'])/'tests'/'agent_cases').glob('*.case.json'):
                    try:values.append(read(p))
                    except (ValueError,OSError):pass
                return self.send_json({'items':values})
            if path.startswith('/api/v1/comparisons/'):
                ident=path.rsplit('/',1)[1]
                try:uuid.UUID(ident);return self.send_json(read(store.directory/'comparisons'/(ident+'.json')))
                except (ValueError,OSError):return self.send_json({'error':{'code':'not_found','message':'Unknown comparison'}},404)
            allowed={'/','/index.html','/legacy.html','/app.js','/core.js','/demo.js','/styles.css','/dashboard.js','/dashboard.css','/v2.js','/regression-demo.json','/debugger.html','/rollout.js','/rollout.css','/rollout-demo.json','/agent-replay-mvp.zip'}
            if path in allowed:
                from http.server import SimpleHTTPRequestHandler
                return SimpleHTTPRequestHandler.do_GET(self)
            return super().do_GET()
        def do_HEAD(self):
            if not self.local_request(): return
            allowed={'/','/index.html','/legacy.html','/app.js','/core.js','/demo.js','/styles.css','/dashboard.js','/dashboard.css','/v2.js','/regression-demo.json','/debugger.html','/rollout.js','/rollout.css','/rollout-demo.json','/agent-replay-mvp.zip'}
            if urlsplit(self.path).path not in allowed: return self.send_json({'error':{'code':'not_found','message':'Unknown resource'}},404)
            from http.server import SimpleHTTPRequestHandler
            return SimpleHTTPRequestHandler.do_HEAD(self)
        def do_POST(self):
            if not urlsplit(self.path).path.startswith('/api/v1/'):return super().do_POST()
            if not self.local_request():return
            if self.headers.get('X-Replay-Token')!=store.token:return self.send_json({'error':{'code':'forbidden','message':'Local session token required'}},403)
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send_json({'error':{'code':'content_type','message':'JSON required'}},415)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=5*1024*1024:return self.send_json({'error':{'code':'too_large','message':'Request exceeds size limit'}},413)
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('JSON object required')
                path=urlsplit(self.path).path;status=200
                if path=='/api/v1/traces/import':
                    trace=to_v2(validate_trace(body['trace']));old=store.traces().get(trace['id'])
                    if old and digest(old)!=digest(trace):return self.send_json({'error':{'code':'conflict','message':'Trace ID already has different content'}},409)
                    save(store.directory/'traces'/(digest(trace)+'.json'),trace);value={'id':trace['id']};status=201
                elif path=='/api/v1/doctor':value=doctor(store.traces()[body['source_id']],project,body['entrypoint_id'])
                elif path=='/api/v1/experiments':
                    trace=store.traces()[body['source_id']];spec=body['spec']
                    if any(k in spec for k in ('code_ref','fixture_set','environment_profile')):raise ValueError('API cannot select files, code refs, or environments; use CLI')
                    spec=resolve_spec(spec,project)
                    if spec['entrypoint_id'] not in project.get('entrypoints',{}):raise ValueError('Unknown entrypoint')
                    value=store.start(body,lambda event:execute(trace,spec,project,store.directory,cancel=event),self.headers.get('Idempotency-Key'));status=202
                elif path.endswith('/cancel'):
                    ident=path.split('/')[-2];store.cancel[ident].set();value={'id':ident,'cancellation_requested':True}
                elif path=='/api/v1/comparisons':
                    traces=store.traces();value=compare(traces[body['source_id']],traces[body['candidate_id']]);save(store.directory/'comparisons'/(value['id']+'.json'),value)
                elif path=='/api/v1/cases':
                    source=store.traces()[body['source_id']]
                    if body['entrypoint_id'] not in project.get('entrypoints',{}):raise ValueError('Unknown entrypoint')
                    temp=store.directory/'traces'/(digest(source)+'.json');save(temp,source)
                    target=Path(project['_root'])/'tests'/'agent_cases'/(str(uuid.uuid4())+'.case.json')
                    value=cases.create(temp,body['entrypoint_id'],target);value['assertions']=body.get('assertions',[]);value['replay_spec']['fixtures']=body.get('fixtures',[]);save(target,value);status=201
                elif path.startswith('/api/v1/cases/') and path.endswith('/accept'):
                    ident=path.split('/')[-2];uuid.UUID(ident)
                    case_path=Path(project['_root'])/'tests'/'agent_cases'/(ident+'.case.json')
                    result_id=body['result_id'];uuid.UUID(result_id)
                    result_path=store.directory/'reports'/(result_id+'.json')
                    value=cases.accept(case_path,result_path)
                elif path=='/api/v1/suites/run':
                    paths=[within(project['_root'],p) for p in project.get('suites',{}).get(body['registered_suite_id'],[])]
                    if body['registered_suite_id']=='created-cases':paths=sorted((Path(project['_root'])/'tests'/'agent_cases').glob('*.case.json'))
                    if not paths:raise ValueError('No registered suite cases')
                    value=store.start(body,lambda event:run_suite(paths,project,store.directory/'reports',cancel=event),self.headers.get('Idempotency-Key'));status=202
                else:return self.send_json({'error':{'code':'not_found','message':'Unknown route'}},404)
                return self.send_json(value,status)
            except RuntimeError as exc:return self.send_json({'error':{'code':'busy','message':str(exc)}},429)
            except (ValueError,KeyError,TypeError,OSError) as exc:return self.send_json({'error':{'code':'invalid_request','message':str(exc)}},400)
    Handler.store=store
    return Handler
