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
from .guard.store import QuotaExceeded

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
            if 'organizations' in p.relative_to(self.directory).parts:
                continue  # A local desktop store cannot discover nested tenant namespaces.
            try:
                t=read(p)
                if isinstance(t,dict) and t.get('schema_version') in ('1.0','2.0') and 'steps' in t:
                    from .security.sanitizer import sanitize_artifact
                    t=to_v2(validate_trace(sanitize_artifact(t)));found[t['id']]=t
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

def make_handler(directory,web_root,project,access=None):
    from .cli import make_handler as legacy_handler
    Base=legacy_handler(str(Path(directory)/'traces'),web_root);store=Store(directory,project)
    tenant_stores={}
    tenant_lock=threading.RLock()
    class Handler(Base):
        def local_request(self):
            if not super().local_request(): return False
            if not access or not urlsplit(self.path).path.startswith('/api/'):
                return True
            from .security.access import AccessDenied
            try:
                self.principal=access.authenticate(self.headers.get('Authorization'))
                access.authorize(self.principal,self.command,urlsplit(self.path).path)
                access.audit(self.principal,self.command+' '+urlsplit(self.path).path)
                return True
            except AccessDenied:
                if hasattr(self,'principal'):
                    access.audit(self.principal,'request','denied')
                self.send_json({'error':{'code':'forbidden','message':'Access denied'}},403)
                return False

        def context(self):
            if not access or not hasattr(self,'principal'): return store,project
            with tenant_lock:
                org=self.principal['org_id']
                if org not in tenant_stores:
                    root=access.directory(self.principal)
                    tenant_stores[org]=Store(root,{'_root':str(root),'entrypoints':{},'suites':{}})
                scoped=tenant_stores[org]
                return scoped,scoped.project

        def send_json(self,value,status=200):
            from .security.sanitizer import sanitize_artifact
            # Local session bootstrap is a control credential, not captured trajectory data.
            if access or urlsplit(self.path).path != '/api/v1/health':
                value=sanitize_artifact(value)
            return super().send_json(value,status)
        def guard_store(self,store):
            from .guard.store import GuardStore
            return GuardStore(store.directory, org_id=self.principal['org_id'] if access else 'local',
                limits=access.guard_limits if access else None,
                sensitive_fields=access.sensitive_fields if access else ())
        def do_GET(self):
            if not self.local_request():return
            store,project=self.context()
            path=urlsplit(self.path).path
            if path.startswith('/api/v1/planning/sessions/'):
                try:
                    from .planning.store import PlanningStore
                    parts=path[len('/api/v1/planning/sessions/'):].split('/')
                    session=PlanningStore(store.directory).get(parts[0])
                    if len(parts)>1:
                        if len(parts)!=2 or parts[1] not in ('candidates','rollouts','outcomes'): raise ValueError('Unknown planning resource')
                        session={'items':session[parts[1]]}
                    return self.send_json(session)
                except (ValueError,OSError,KeyError): return self.send_json({'error':'Planning session unavailable'},404)
            if path.startswith('/api/v1/guard/'):
                guard=self.guard_store(store)
                try:
                    if path=='/api/v1/guard/cases':return self.send_json({'items':guard.list_cases()})
                    if path=='/api/v1/guard/usage':return self.send_json(guard.usage())
                    if path.startswith('/api/v1/guard/cases/'):return self.send_json(guard.get_case(path.rsplit('/',1)[1]))
                    if path.startswith('/api/v1/guard/runs/'):
                        ident=path.rsplit('/',1)[1];uuid.UUID(ident)
                        return self.send_json(read(within(store.directory,'guard/runs/'+ident+'/gate.json')))
                except (ValueError,OSError,KeyError,TypeError):
                    return self.send_json({'error':{'code':'not_found','message':'Unknown guard resource'}},404)
            if path=='/api/v1/health' and access:
                return self.send_json({'service':'agent-replay','capabilities':['inspect','safe-replay','compare'], 'entrypoints':[],'suites':[], 'organization':self.principal['org_id'],'role':self.principal['role'],'retention_days':access.retention_days})
            if path=='/api/v1/health':return self.send_json({'service':'agent-replay','version':'0.2.0','schema_versions':['1.0','2.0'],'session_token':store.token,'capabilities':['inspect','run','compare','case','suite'],'entrypoints':list(project.get('entrypoints',{})),'suites':list(project.get('suites',{}))+['created-cases']})
            if path=='/api/v1/traces':
                query=parse_qs(urlsplit(self.path).query)
                try:offset=max(0,int(query.get('cursor',['0'])[0]));limit=min(100,max(1,int(query.get('limit',['100'])[0])))
                except ValueError:return self.send_json({'error':{'code':'invalid_query','message':'Invalid cursor'}},400)
                values=list(store.traces().values());page=values[offset:offset+limit]
                return self.send_json({'items':page,'next_cursor':str(offset+limit) if offset+limit<len(values) else None})
            if path=='/api/v1/failures':return self.send_json({'items':[t for t in store.traces().values() if t['execution']['status']=='error' or t.get('failures')]})
            if path.startswith('/api/v1/traces/') and path.endswith('/export'):
                t=store.traces().get(path.split('/')[-2])
                return self.send_json(t if t else {'error':'Not found'},200 if t else 404)
            if path=='/api/v1/benchmarks':
                return self.send_json({'items':[read(p) for p in (store.directory/'benchmarks').glob('*/run.json')]})
            if path=='/api/v1/policies':
                target=store.directory/'policies.json'
                return self.send_json(read(target) if target.exists() else {'release':{'environment':'PRODUCTION'}})
            if path.startswith('/api/v1/traces/'):
                t=store.traces().get(path.rsplit('/',1)[1]);return self.send_json(t if t else {'error':{'code':'not_found','message':'Unknown trace'}},200 if t else 404)
            if path.startswith('/api/v1/experiments/') or path.startswith('/api/v1/suites/'):
                job=store.jobs.get(path.rsplit('/',1)[1]);return self.send_json(job or {'error':{'code':'not_found','message':'Unknown job'}},200 if job else 404)
            if path=='/api/v1/results':
                values=[];gates=[]
                for p in (store.directory/'reports').glob('*.json'):
                    try:
                        value=read(p)
                        if value.get('case_id') and value.get('verdict'): values.append(value)
                        elif value.get('decision') in ('PASS','FAIL','INCONCLUSIVE'): gates.append(value)
                    except (ValueError,OSError,AttributeError): pass
                return self.send_json({'items':values,'quality_gates':gates})
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
            allowed={'/','/index.html','/legacy.html','/app.js','/core.js','/demo.js','/styles.css','/dashboard.js','/dashboard.css','/workspace-loader.js','/planning-demo.json','/planning.js','/guard-ui.js','/guard-ui.css','/v2.js','/regression-demo.json','/debugger.html','/rollout.js','/rollout.css','/rollout-demo.json','/benchmark.html','/benchmark.js','/benchmark-results.json','/agent-replay-mvp.zip'}
            if path in allowed:
                from http.server import SimpleHTTPRequestHandler
                return SimpleHTTPRequestHandler.do_GET(self)
            return super().do_GET()
        def do_HEAD(self):
            if not self.local_request(): return
            allowed={'/','/index.html','/legacy.html','/app.js','/core.js','/demo.js','/styles.css','/dashboard.js','/dashboard.css','/workspace-loader.js','/planning-demo.json','/planning.js','/guard-ui.js','/guard-ui.css','/v2.js','/regression-demo.json','/debugger.html','/rollout.js','/rollout.css','/rollout-demo.json','/benchmark.html','/benchmark.js','/benchmark-results.json','/agent-replay-mvp.zip'}
            if urlsplit(self.path).path not in allowed: return self.send_json({'error':{'code':'not_found','message':'Unknown resource'}},404)
            from http.server import SimpleHTTPRequestHandler
            return SimpleHTTPRequestHandler.do_HEAD(self)
        def do_POST(self):
            self.connection.settimeout(15)
            with tenant_lock:
                return self._do_POST()

        def _do_POST(self):
            if not urlsplit(self.path).path.startswith('/api/v1/'):return super().do_POST()
            if not self.local_request():return
            store,project=self.context()
            if not access and self.headers.get('X-Replay-Token')!=store.token:return self.send_json({'error':{'code':'forbidden','message':'Local session token required'}},403)
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send_json({'error':{'code':'content_type','message':'JSON required'}},415)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=5*1024*1024:return self.send_json({'error':{'code':'too_large','message':'Request exceeds size limit'}},413)
                try:body=json.loads(self.rfile.read(size))
                except (ValueError,RecursionError):raise ValueError('Malformed JSON document') from None
                if not isinstance(body,dict):raise ValueError('JSON object required')
                path=urlsplit(self.path).path;status=200
                if access and not path.startswith('/api/v1/guard/') and path not in ('/api/v1/traces/import','/api/v1/safe-replay','/api/v1/retention'):
                    # Existing artifact-producing routes share the tenant byte cap.
                    self.guard_store(store).ensure_storage(5*1024*1024)
                if path=='/api/v1/planning/simulate':
                    from .planning.api import simulate
                    value=simulate(body,store.directory,access.sensitive_fields if access else ());status=201
                elif path.startswith('/api/v1/guard/'):
                    guard=self.guard_store(store)
                    if path=='/api/v1/guard/import':
                        value=guard.import_trace(body['trace'],body.get('format','native'));status=201
                    elif path=='/api/v1/guard/cases':
                        value=guard.generate(body['source_id'],body.get('positive_id'),body.get('expected'),body.get('case_id'));status=201
                    elif path.startswith('/api/v1/guard/cases/') and path.endswith('/approve'):
                        value=guard.approve(path.split('/')[-2], self.principal['subject'] if access else body.get('reviewer'),body.get('version'))
                    elif path=='/api/v1/guard/runs':
                        from .guard.runner import run as run_guard
                        value=run_guard(guard,body['case_id'],body.get('baseline_id'),body.get('candidate_id'),body.get('version'));status=201
                    else:return self.send_json({'error':{'code':'not_found','message':'Unknown guard route'}},404)
                elif path=='/api/v1/benchmark-gate' and access:
                    from .benchmark.release import release_gate
                    from .security.access import AccessDenied
                    # Run IDs resolve only in the authenticated organization namespace.
                    for key in ('baseline','candidate'):
                        import re
                        if not isinstance(body.get(key),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',body[key]):
                            raise ValueError('Invalid benchmark run ID')
                    policy_path=store.directory/'policies.json'
                    configured=read(policy_path).get('release',{}) if policy_path.exists() else {}
                    value=release_gate(within(store.directory/'benchmarks',body['baseline']),within(store.directory/'benchmarks',body['candidate']),configured)
                    save(store.directory/'reports'/('gate-'+str(uuid.uuid4())+'.json'),value)
                elif path=='/api/v1/policies' and access:
                    from .benchmark.release import release_gate
                    # Validation runs before any artifact is changed.
                    release_gate(store.directory/'missing-baseline',store.directory/'missing-candidate',body['release'])
                    value={'release':body['release']}
                    save(store.directory/'policies.json',value)
                elif path=='/api/v1/retention' and access:
                    if any(j['state'] in ('queued','running') for j in store.jobs.values()):
                        raise ValueError('Cannot retain while jobs are active')
                    value=access.retain(self.principal,body.get('apply') is True)
                    if not value['dry_run'] and value['eligible']:
                        store.jobs.clear();store.cancel.clear();store.idempotency.clear()
                elif path=='/api/v1/safe-replay':
                    from .security.replay import playback
                    value=playback(store.traces()[body['source_id']],body.get('mode','MOCK_REPLAY'),body.get('kind','TRACE_PLAYBACK'))
                    if access:
                        guard=self.guard_store(store);guard.import_trace(value);value=read(guard.trace_path(value['id']))
                    else:save(store.directory/'traces'/(value['id']+'.json'),value)
                elif path=='/api/v1/traces/import':
                    if access:
                        value=self.guard_store(store).import_trace(body['trace']);return self.send_json(value,201)
                    from .security.sanitizer import sanitize_artifact
                    trace=to_v2(validate_trace(sanitize_artifact(body['trace'])));old=store.traces().get(trace['id'])
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
            except QuotaExceeded as exc:return self.send_json({'error':{'code':'quota_exceeded','message':str(exc)}},429)
            except RuntimeError as exc:return self.send_json({'error':{'code':'busy','message':str(exc)}},429)
            except (ValueError,KeyError,TypeError,OSError) as exc:return self.send_json({'error':{'code':'invalid_request','message':str(exc)}},400)
    Handler.store=store
    return Handler
