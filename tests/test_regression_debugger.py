import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from agent_replay import capture,tool
from agent_replay.schema import validate_trace
from agent_replay.storage import read,save
from agent_replay.journal import recover
from agent_replay.bundle import export_bundle,import_bundle
from agent_replay.migrations import to_v2
from agent_replay.demo import demo
from agent_replay.comparison import compare
from agent_replay.regression.assertions import evaluate
from agent_replay.regression.cases import accept
from agent_replay.regression.runner import run_suite
from agent_replay.project import load_project
from agent_replay.runtime.matcher import Matcher
from agent_replay.runtime.fixtures import Fixtures
from agent_replay.replay import ReplayMismatch
from agent_replay.providers.anthropic import normalize
from agent_replay.experiments import execute

class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.story=demo(cls.root/'demo')
        cls.project=load_project(cls.story['project']);cls.case=Path(cls.story['case'])
        cls.source=read(next((cls.root/'demo'/'traces').glob('*.json')))
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_real_fail_pass_fail(self):
        self.assertEqual([self.story[k]['exit_code'] for k in ('before','after','regressed')],[1,0,1])
        self.assertEqual(self.story['after']['results'][0]['assertions'][2]['actual'],[2900])
    def test_skipping_action_does_not_pass(self):
        data=read(self.case);data['entrypoint_id']='skipped';path=self.case.parent/'skipped.case.json';save(path,data)
        report=run_suite([path],self.project,self.root/'skipped-report')
        self.assertNotEqual(report['exit_code'],0)
    def test_empty_suite_and_assertions_do_not_pass(self):
        with self.assertRaises(ValueError):run_suite([],self.project,self.root/'empty')
        data=read(self.case);data['assertions']=[];path=self.case.parent/'empty.case.json';save(path,data)
        self.assertEqual(run_suite([path],self.project,self.root/'empty-report')['exit_code'],2)
    def test_business_failure_separate_from_execution(self):
        self.assertEqual(self.source['execution']['status'],'completed');self.assertTrue(self.source['failures'])
        self.assertEqual(self.source['evaluation']['verdict'],'not_evaluated')
    def test_v2_round_trip_and_cycles(self):
        validate_trace(self.source);bad=copy.deepcopy(self.source);bad['steps'][0]['parent_id']=bad['steps'][0]['id']
        with self.assertRaises(ValueError):validate_trace(bad)
    def test_v1_not_mutated(self):
        with capture('legacy',self.root/'legacy') as run:
            with run.span('one',input=None) as span:span.set_output(None)
        raw=Path(run.path).read_bytes();t=to_v2(read(run.path));self.assertFalse(t['output']['present']);self.assertTrue(t['steps'][0]['output']['present']);self.assertEqual(Path(run.path).read_bytes(),raw)
    def test_capture_preserves_values_and_redacts_before_journal(self):
        @tool
        def original():return {'key':'sensitive'}
        with capture('private',self.root/'private'/'traces',input={},durability='sync',redactor=lambda x:json.loads(json.dumps(x).replace('sensitive','[REDACTED]'))) as run:
            self.assertEqual(original(),{'key':'sensitive'})
        for path in (self.root/'private').rglob('*'):
            if path.is_file():self.assertNotIn('sensitive',path.read_text())
    def test_journal_recovers_prefix(self):
        journal=next((self.root/'demo'/'journals').glob('*.jsonl'));lines=journal.read_bytes().splitlines(keepends=True)
        path=self.root/'partial.jsonl';path.write_bytes(b''.join(lines[:-1])+b'{bad')
        t=recover(path);self.assertEqual(t['execution']['status'],'interrupted');self.assertEqual(t['capture_health']['state'],'partial');validate_trace(t)
    def test_bundle_roundtrip_and_checksum(self):
        source=next((self.root/'demo'/'traces').glob('*.json'));bundle=self.root/'good.zip';export_bundle(source,bundle)
        imported=import_bundle(bundle,self.root/'imports');self.assertEqual(read(imported['path'])['id'],self.source['id'])
        malicious=self.root/'bad.zip'
        with zipfile.ZipFile(malicious,'w') as z:z.writestr('../escape','x');z.writestr('trace.json','{}');z.writestr('manifest.json','{}')
        with self.assertRaises(ValueError):import_bundle(malicious,self.root/'imports')
        self.assertFalse((self.root/'escape').exists())
    def test_matcher_rejects_ambiguity_and_changed_input(self):
        s={'id':'a','name':'x','kind':'tool','input':{'a':1},'status':'success'}
        with self.assertRaises(ReplayMismatch):Matcher([s,{**s,'id':'b'}]).take(s)
        with self.assertRaises(ReplayMismatch):Matcher([s]).take({**s,'input':{'a':2}})
    def test_fixture_null_and_exhaustion(self):
        f=Fixtures([{'id':'x','selector':{'name':'x'},'consume':'once','response':{'kind':'return','value':None}}])
        self.assertIsNone(f.resolve({'name':'x'})['value'])
        with self.assertRaises(ReplayMismatch):f.resolve({'name':'x'})
    def test_diff_finds_changed_argument(self):
        candidate=read(next((self.root/'demo'/'after'/'runs'/'traces').glob('*.json')))
        report=compare(self.source,candidate)
        paths=[r['path'] for p in report['field_changes'] for r in p['changes']]
        self.assertIn('/input/value/amount_minor',paths);self.assertEqual(report['coverage']['aligned'],2)
    def test_missing_numeric_and_boolean(self):
        rule={'id':'amount','path':'/missing','op':'lte','expected':3000}
        self.assertEqual(evaluate(self.source,[rule])[0]['verdict'],'fail')
        rule['path']='/output/present';self.assertEqual(evaluate(self.source,[rule])[0]['verdict'],'fail')
    def test_baseline_accept_requires_exact_passing_run(self):
        result=self.story['after']['results'][0];path=self.root/'pass.json';save(path,result)
        accepted=accept(self.case,path);self.assertIn('baseline',accepted)
        failed=self.root/'fail.json';save(failed,self.story['before']['results'][0])
        with self.assertRaises(ValueError):accept(self.case,failed)
        changed=read(self.case);changed['assertions'][2]['expected']=4000;save(self.case,changed)
        report=run_suite([self.case],self.project,self.root/'stale-report');self.assertEqual(report['results'][0]['baseline_state'],'stale')
        changed['assertions'][2]['expected']=3000;save(self.case,changed)
    def test_anthropic_tool_roundtrip_request(self):
        body=normalize({'model':'model-b','messages':[{'role':'system','content':'You help.'},{'role':'user','content':'lookup'},{'role':'assistant','content':None,'tool_calls':[{'id':'t1','function':{'name':'lookup','arguments':'{"id":"1"}'}}]},{'role':'tool','tool_call_id':'t1','content':'ok'}],'max_tokens':20})
        self.assertEqual(body['messages'][1]['content'][0]['type'],'tool_use');self.assertEqual(body['messages'][2]['content'][0]['type'],'tool_result')
        with self.assertRaises(ValueError):normalize({'model':'x','messages':[],'stream':True})
    def test_worker_timeout_does_not_hang(self):
        project=copy.deepcopy(self.project);project['entrypoints']['slow']={'callable':'time:sleep'}
        spec={'entrypoint_id':'slow','limits':{'timeout_seconds':.001}}
        result=execute(self.source,spec,project,self.root/'timeout')
        self.assertEqual(result['state'],'interrupted')
    def test_cli_json_and_error_exit(self):
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'sdk')}
        p=subprocess.run([sys.executable,'-m','agent_replay.cli','test',str(self.root/'nonexistent'),'--project',self.story['project']],env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,2);self.assertIn('empty',json.loads(p.stderr)['error'])
    def test_code_ref_runs_distinct_files(self):
        root=self.root/'git-app';root.mkdir(exist_ok=True)
        def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL,text=True).strip()
        git('init');git('config','user.email','test@example.invalid');git('config','user.name','Test')
        (root/'app.py').write_text('def agent(run): return {"marker": "old"}\n');git('add','.');git('commit','-m','old');old=git('rev-parse','HEAD')
        (root/'app.py').write_text('def agent(run): return {"marker": "new"}\n');git('add','.');git('commit','-m','new');new=git('rev-parse','HEAD')
        project={'_root':str(root),'entrypoints':{'agent':{'callable':'app:agent'}}}
        a=execute(self.source,{'entrypoint_id':'agent','code_ref':old},project,self.root/'code-a');b=execute(self.source,{'entrypoint_id':'agent','code_ref':new},project,self.root/'code-b')
        self.assertEqual(a['trace']['output']['value']['marker'],'old');self.assertEqual(b['trace']['output']['value']['marker'],'new');self.assertEqual(git('rev-parse','HEAD'),new)

class EdgeTests(unittest.TestCase):
    def test_async_replay_inside_event_loop(self):
        from agent_replay import async_replay_agent
        async def main(root):
            @tool
            async def lookup():return 42
            with capture('async',root) as original:await lookup()
            async def app(run):return await lookup()
            replay=await async_replay_agent(original.trace,app,directory=root)
            self.assertEqual(replay.trace['output'],42)
        with tempfile.TemporaryDirectory() as root:asyncio.run(main(root))
    def test_model_ir_roundtrip(self):
        from agent_replay.providers.normalize import to_chat,from_chat
        ir={'model':'x','messages':[{'role':'user','content':[{'type':'text','text':'hello'}]}]}
        self.assertEqual(to_chat(ir)['messages'][0]['content'],'hello')
        response=from_chat({'choices':[{'message':{'role':'assistant','tool_calls':[{'id':'t','function':{'name':'lookup','arguments':'{"id":1}'}}]}}]})
        self.assertEqual(response['content'][0]['arguments'],{'id':1})
    def test_redacted_output_cannot_be_frozen(self):
        from agent_replay.runtime.policy import Policy
        from agent_replay.migrations import legacy
        with tempfile.TemporaryDirectory() as root:
            @tool
            def lookup():return {'api_key':'secret'}
            with capture('redacted',root,input={}) as captured:lookup()
            trace=legacy(read(captured.path));policy=Policy(trace,{},{});policy.run=type('Run',(),{'_current_event':trace['steps'][0],'trace':trace})()
            with self.assertRaises(ReplayMismatch):policy.resolve('lookup','tool',{})
    def test_live_rules_blocked_in_default_ci(self):
        with tempfile.TemporaryDirectory() as root:
            story=demo(root);path=Path(story['case']);case=read(path)
            case['replay_spec']['model_rules']=[{'selector':{'name':'chat.completions'},'mode':'live','provider_id':'test','model':'x'}]
            save(path,case);project=load_project(story['project']);project['providers']={'test':{'kind':'openai_compatible'}}
            result=run_suite([path],project,Path(root)/'blocked');self.assertEqual(result['exit_code'],2);self.assertIn('--live',result['results'][0]['reason'])
    def test_no_vacuous_pass_after_caught_mismatch(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);story=demo(root);project=load_project(story['project'])
            (root/'caught.py').write_text('from agent_replay import tool\n@tool\ndef unknown(): return 1\ndef agent(run):\n try: unknown()\n except Exception: pass\n return {"ok": True}\n')
            project['entrypoints']['caught']={'callable':'caught:agent'};case=read(story['case']);case['entrypoint_id']='caught';case['replay_spec'].pop('fixture_set');case['assertions']=[{'id':'ok','op':'equals','path':'/output/value/ok','expected':True}];path=root/'caught.case.json';save(path,case)
            result=run_suite([path],project,root/'caught-report');self.assertEqual(result['exit_code'],3)
    def test_source_retention_protection(self):
        from agent_replay.retention import cleanup
        import time
        with tempfile.TemporaryDirectory() as root:
            story=demo(root);case=read(story['case']);path=Path(root)/case['source']['trace_path'];os.utime(path,(1,1));result=cleanup(root,root,1,False);self.assertNotIn(str(path),result['files'])
