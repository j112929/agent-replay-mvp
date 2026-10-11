import {test} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile, readdir, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {spawn} from 'node:child_process';
import {fixture,samples} from './helpers.mjs';

test('signup → sanitized imports → verified approval → safe replay → persisted gate and remote CI',async t=>{
  const f=await fixture();t.after(f.stop);const owner=await f.signup(), org=await f.org(owner), project=await f.project(owner,org.id), base='/api/saas/projects/'+project.id;
  const key=randomUUID();
  const a=await f.request(base+'/import',{trace:samples.traces[0],format:'generic'},owner,{'Idempotency-Key':key});assert.equal(a.status,200);
  const repeat=await f.request(base+'/import',{trace:samples.traces[0],format:'generic'},owner,{'Idempotency-Key':key});assert.deepEqual(repeat.data,a.data);
  assert.equal((await f.request(base+'/import',{trace:samples.traces[1],format:'generic'},owner,{'Idempotency-Key':key})).status,409);
  const b=await f.request(base+'/import',{trace:samples.traces[1],format:'generic'},owner);assert.equal(b.status,200);
  const trace=await f.request(base+'/traces/'+b.data.id,undefined,owner);
  assert.equal(trace.data.privacy.status,'sanitized');assert.ok(trace.data.privacy.redacted_field_count > 0);
  assert.ok(!JSON.stringify(trace.data).includes('sk-syntheticGuardToken'));
  const generated=await f.request(base+'/generate',{source:b.data.id,positive:a.data.id,expected:samples.expected},owner);
  assert.equal(generated.status,200);assert.equal(generated.data.case.guard.validation.status,'validated');assert.equal(generated.data.review.state,'draft');
  const id=generated.data.case.id;
  const incomplete=await f.request(base+'/evaluate',{case_id:id,baseline:a.data.id,candidate:b.data.id},owner);assert.equal(incomplete.data.decision,'INCONCLUSIVE');
  const approved=await f.request(base+'/approve',{case_id:id,reviewer:'forged'},owner);assert.equal(approved.status,200);assert.notEqual(approved.data.reviewer,'forged');
  const gate=await f.request(base+'/evaluate',{case_id:id,baseline:a.data.id,candidate:b.data.id},owner);assert.equal(gate.status,200);assert.equal(gate.data.decision,'FAIL');assert.equal(gate.data.classification,'regression');assert.equal(gate.data.external_operations,0);assert.equal(gate.data.model_calls,0);
  const pass=await f.request(base+'/evaluate',{case_id:id,baseline:a.data.id,candidate:a.data.id},owner);assert.equal(pass.data.decision,'PASS');
  const playback=await f.request(base+'/traces/'+b.data.id+'/playback',undefined,owner);assert.equal(playback.data.replay.external_operations,0);assert.ok(playback.data.replay.blocked_operations.every(o=>o.real_execution==='DENY'));
  assert.equal((await f.request(base+'/runs/'+gate.data.id+'/report',undefined,owner)).status,200);
  await f.request(base+'/control',{case_id:id,operation:'disable'},owner);
  const disabled=await f.request(base+'/evaluate',{case_id:id,baseline:a.data.id,candidate:a.data.id},owner);
  assert.equal(disabled.data.decision,'INCONCLUSIVE');assert.equal(disabled.data.blocking,true);
  await f.request(base+'/control',{case_id:id,operation:'enable'},owner);
  assert.equal((await f.request(base+'/cases/'+id,undefined,owner)).data.review.state,'approved');
  const suite=await f.request(base+'/suites',{name:'Release guard',cases:[{id,version:1}]},owner);assert.equal(suite.status,201);
  const suiteRun=await f.request(base+'/suites/'+suite.data.id+'/execute',{evidence:{[id]:{baseline:a.data.id,candidate:b.data.id}}},owner);assert.equal(suiteRun.data.exit_code,1);
  const apiKey=(await f.request(base+'/keys',{name:'CI secret'},owner)).data.key;
  const baseline=join(f.config.directory,'baseline.json'), candidate=join(f.config.directory,'candidate.json'), output=join(f.config.directory,'ci-result.json');
  await writeFile(baseline,JSON.stringify(samples.traces[0]));await writeFile(candidate,JSON.stringify(samples.traces[1]));
  const cli=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,['saas/ci.mjs','--server',f.config.origin,'--project',project.id,'--case',id,'--baseline',baseline,'--candidate',candidate,'--output',output],{env:{...process.env,GUARD_API_KEY:apiKey}});let stdout='',stderr='';child.stdout.on('data',d=>stdout+=d);child.stderr.on('data',d=>stderr+=d);child.on('error',reject);child.on('close',code=>resolve({code,stdout,stderr}));});
  assert.equal(cli.code,1);assert.equal(JSON.parse(cli.stdout).decision,'FAIL');assert.ok(!cli.stdout.includes(apiKey));assert.ok(!cli.stdout.includes('sk-syntheticGuardToken'));assert.equal(JSON.parse(await readFile(output,'utf8')).blocking,true);
  const usage=(await f.request('/api/saas/organizations/'+org.id+'/usage',undefined,owner)).data;assert.equal(usage.imports,2);assert.equal(usage.cases,1);assert.equal(usage.model_calls,0);
  for(const file of await readdir(join(f.config.directory,'artifacts'),{recursive:true})) {const path=join(f.config.directory,'artifacts',file);if(file.endsWith('.json'))assert.ok(!(await readFile(path,'utf8')).includes('sk-syntheticGuardToken'));}
  assert.ok(f.app.db.prepare('SELECT count(*) n FROM guard_audit WHERE org_id=?').get(org.id).n>0);
});

test('rejected versions cannot acquire approval or bypass review by enabling',async t=>{
  const f=await fixture();t.after(f.stop);const owner=await f.signup(),org=await f.org(owner),p=await f.project(owner,org.id),base='/api/saas/projects/'+p.id;
  for(const trace of samples.traces)await f.request(base+'/import',{trace,format:'generic'},owner);
  const generated=(await f.request(base+'/generate',{source:'coding-B',positive:'coding-A',expected:samples.expected},owner)).data.case;
  assert.equal((await f.request(base+'/control',{case_id:generated.id,operation:'reject'},owner)).status,200);
  assert.equal((await f.request(base+'/approve',{case_id:generated.id},owner)).status,422);
  assert.equal((await f.request(base+'/control',{case_id:generated.id,operation:'enable'},owner)).status,422);
  const replacement=(await f.request(base+'/generate',{case_id:generated.id,source:'coding-B',positive:'coding-A',expected:samples.expected},owner)).data;
  assert.equal(replacement.case.guard.version,2);assert.equal(replacement.review.state,'draft');
});

test('malformed, oversized and nested imports fail safely; quotas are server enforced',async t=>{
  const f=await fixture();t.after(f.stop);const owner=await f.signup(),org=await f.org(owner),p=await f.project(owner,org.id),base='/api/saas/projects/'+p.id;
  assert.equal((await f.request(base+'/import',{trace:{bad:'sk-syntheticDoNotLeak'},format:'generic'},owner)).status,422);
  assert.equal(f.app.service.usage(org.id).imports,0);
  const huge=await f.request(base+'/import',{trace:{output:'x'.repeat(1200000)}},owner);assert.equal(huge.status,413);
  let nested={secret:'sk-syntheticDoNotLeak'};for(let i=0;i<60;i++)nested={nested};
  const invalid=await f.request(base+'/import',{trace:nested},owner);assert.equal(invalid.status,400);assert.ok(!JSON.stringify(invalid.data).includes('sk-syntheticDoNotLeak'));
  f.config.plans.trial.imports=1;
  assert.equal((await f.request(base+'/import',{trace:samples.traces[0],format:'generic'},owner)).status,200);
  assert.equal((await f.request(base+'/import',{trace:samples.traces[1],format:'generic'},owner)).status,429);
  f.config.plans.trial.storage_bytes=1;
  assert.equal((await f.request(base+'/generate',{source:'coding-B'},owner)).status,429);
});

test('organization custom redaction cannot disable mandatory credentials protection',async t=>{
  const f=await fixture();t.after(f.stop);const owner=await f.signup(),org=await f.org(owner),p=await f.project(owner,org.id);
  assert.equal((await f.request('/api/saas/organizations/'+org.id+'/privacy',{sensitive_fields:['customer_identifier']},owner)).status,200);
  const trace=structuredClone(samples.traces[0]);trace.id='custom-private';trace.metadata.customer_identifier='synthetic-private-customer';
  assert.equal((await f.request('/api/saas/projects/'+p.id+'/import',{trace,format:'generic'},owner)).status,200);
  const sanitized=(await f.request('/api/saas/projects/'+p.id+'/traces/'+trace.id,undefined,owner)).data;
  assert.equal(sanitized.metadata.customer_identifier,'[REDACTED]');
  assert.ok(!JSON.stringify(sanitized).includes('sk-syntheticGuardToken'));
  assert.equal((await f.request('/api/saas/organizations/'+org.id+'/privacy',{sensitive_fields:[],required_credentials_redaction:false},owner)).status,200);
  const policy=(await f.request('/api/saas/organizations/'+org.id+'/privacy',undefined,owner)).data;
  assert.equal(policy.required_credentials_redaction,true);
});
