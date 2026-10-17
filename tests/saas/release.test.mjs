import {test} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,randomUUID} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fixture} from './helpers.mjs';

const execute=promisify(execFile);
const configuration={model:'synthetic',prompt_version:'addition.v1',tools_version:'memory.v1',environment_version:'toy.v1'};

test('administrator release target → actual fresh CI capture → signed gate, replay protection and budget',async t=>{
  if(process.platform!=='linux'){t.skip('SaaS worker enforces Linux address-space limits; full flow runs in Linux CI');return;}
  const f=await fixture({python:'python3'});t.after(f.stop);
  const owner=await f.signup(),org=await f.org(owner),project=await f.project(owner,org.id);
  const base='/api/saas/projects/'+project.id,repo=join(f.config.directory,'checkout'),sdk=resolve('sdk');
  await mkdir(repo);
  await execute('git',['init','-q',repo]);
  await execute('git',['-C',repo,'-c','core.hooksPath=/dev/null','-c','user.name=Release Test','-c','user.email=release@example.invalid','commit','--allow-empty','-qm','Synthetic checkout']);
  const commit=(await execute('git',['-C',repo,'rev-parse','HEAD'])).stdout.trim();
  const env={...process.env,PYTHONPATH:sdk,RELEASE_ROOT:f.config.directory};
  const captures=JSON.parse((await execute('python3',['-c',`
import json,os
from pathlib import Path
from agent_replay.guard.demo import capture_candidate
root=Path(os.environ['RELEASE_ROOT'])
print(json.dumps([capture_candidate(v,root/('seed-'+v)) for v in ('A','B')]))
`],{cwd:repo,env})).stdout);
  const ids=[];
  for(const trace of captures){
    const imported=await f.request(base+'/import',{trace,format:'native'},owner);
    assert.equal(imported.status,200,JSON.stringify(imported.data));ids.push(imported.data.id);
  }
  const generated=await f.request(base+'/generate',{source:ids[1],positive:ids[0],expected:{output:{test_value:5,expression:'a + b'}}},owner);
  assert.equal(generated.status,200);const caseId=generated.data.case.id;
  await f.request(base+'/approve',{case_id:caseId},owner);
  const legacy=await f.request(base+'/evaluate',{case_id:caseId,baseline:ids[0],candidate:ids[0]},owner);
  assert.equal(legacy.data.decision,'PASS');assert.equal(legacy.data.release_eligible,false);
  const apiKey=(await f.request(base+'/keys',{name:'CI runner'},owner)).data.key;
  const auth={Authorization:'Bearer '+apiKey};
  const target={case_id:caseId,baseline:ids[0],commit,candidate_version:'A',configuration,max_cost_usd:1};
  assert.equal((await f.request(base+'/release/challenges',target,auth)).status,403);
  assert.equal((await f.request(base+'/release/challenges',target,owner)).status,422); // no operator trust yet
  const secret=randomBytes(32).toString('hex'),keyFile=join(f.config.directory,'operator-keys.json');
  await writeFile(keyFile,JSON.stringify({[org.id+'/'+project.id]:{runner_id:'trusted-ci',secret}}),{mode:0o600});
  f.config.evidenceKeysFile=keyFile;
  const fresh=async cost=>{
    const response=await f.request(base+'/release/challenges',target,owner);assert.equal(response.status,201);
    const challengeFile=join(f.config.directory,'challenge.json'),proofFile=join(f.config.directory,'proof.json');
    await writeFile(challengeFile,JSON.stringify(response.data));
    await execute('python3',['-c',`
import json,os
from pathlib import Path
from agent_replay.guard.demo import capture_candidate
from agent_replay.guard.evidence import capture_context,attest
from agent_replay.storage import read,save
root=Path(os.environ['RELEASE_ROOT']);c=read(root/'challenge.json');config=json.loads(os.environ['RELEASE_CONFIG'])
trace=capture_candidate('A',root/'fresh',capture_context(c,'run-123',config))
bundle=attest(c,trace,os.environ['GUARD_EVIDENCE_SIGNING_KEY'],Path.cwd(),'run-123',config,float(os.environ['RELEASE_COST']))
save(root/'proof.json',bundle);save(root/'candidate.json',bundle['trace'])
`],{cwd:repo,env:{...env,GUARD_EVIDENCE_SIGNING_KEY:secret,RELEASE_CONFIG:JSON.stringify(configuration),RELEASE_COST:String(cost)}});
    return {proof:JSON.parse(await readFile(proofFile,'utf8')),proofFile};
  };
  const {proof,proofFile}=await fresh(0);
  const baselineFile=join(f.config.directory,'baseline.json');await writeFile(baselineFile,JSON.stringify(captures[0]));
  const client=await execute(process.execPath,['saas/ci.mjs','--server',f.config.origin,'--project',project.id,'--case',caseId,
    '--baseline',baselineFile,'--candidate',join(f.config.directory,'candidate.json'),'--release-evidence',proofFile],{env:{...process.env,GUARD_API_KEY:apiKey}});
  const result=JSON.parse(client.stdout);assert.equal(result.decision,'PASS');assert.equal(result.release_eligible,true);
  assert.ok(!client.stdout.includes(secret));assert.ok(!client.stdout.includes(apiKey));
  const replay=await f.request(base+'/release',{case_id:caseId,baseline:ids[0],candidate:proof.manifest.trace_id,evidence:proof},auth);
  assert.equal(replay.status,200);assert.equal(replay.data.decision,'INCONCLUSIVE');assert.equal(replay.data.release_eligible,false);
  const expensive=(await fresh(2)).proof;
  await f.request(base+'/import',{trace:expensive.trace},auth);
  const request={case_id:caseId,baseline:ids[0],candidate:expensive.manifest.trace_id,evidence:expensive};
  const requestKey=randomUUID(),overBudget=await f.request(base+'/release',request,auth,{'Idempotency-Key':requestKey});
  assert.equal(overBudget.data.decision,'FAIL');assert.equal(overBudget.data.release_evidence.verified,true);
  assert.deepEqual((await f.request(base+'/release',request,auth,{'Idempotency-Key':requestKey})).data,overBudget.data);
});
