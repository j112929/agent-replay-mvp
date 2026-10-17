import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './helpers.mjs';

test('planning API is mock-only, scoped, metered and idempotent',async t=>{
  const f=await fixture();t.after(f.stop);const user=await f.signup(),org=await f.org(user),p=await f.project(user,org.id);
  const base='/api/saas/projects/'+p.id+'/planning';
  await f.request('/api/saas/organizations/'+org.id+'/privacy',{sensitive_fields:['customer_note']},user);
  const body={context:{task_id:'api-task',task_group:'api-group',goal:'Deploy',state:{tests_run:false,compat_checked:false,remaining_steps:6},state_version:0,environment_version:'deployment-memory.v1'},config:{enabled:true}};
  const options={'Idempotency-Key':'planning-api-test'};
  const simulated=await f.request(base+'/simulate',body,user,options);assert.equal(simulated.status,200);
  assert.equal(simulated.data.real_execution,null);assert.equal(simulated.data.execution_kind,'HYPOTHETICAL_ONLY');
  const again=await f.request(base+'/simulate',body,user,options);assert.equal(again.data.session_id,simulated.data.session_id);
  const id=simulated.data.session_id;
  for(const suffix of ['','/candidates','/rollouts','/outcomes'])assert.equal((await f.request(base+'/sessions/'+id+suffix,undefined,user)).status,200);
  assert.equal(f.app.service.usage(org.id).runs,1);
  const other=await f.signup('other-planner@example.invalid');
  assert.equal((await f.request(base+'/sessions/'+id,undefined,other)).status,403);
  assert.equal((await f.request(base+'/simulate',{...body,provider_url:'https://production.invalid'},user)).status,422);
  assert.equal((await f.request(base+'/simulate',{...body,config:{enabled:false}},user)).status,422);
  const privateBody={...body,context:{...body.context,state:{...body.context.state,customer_note:'synthetic confidential account detail',api_key:'sk-synthetic-credential-1234567890'}}};
  const sanitized=await f.request(base+'/simulate',privateBody,user);
  assert.equal(sanitized.status,200);assert.equal(sanitized.data.origin.state.customer_note,'[REDACTED]');
  assert.ok(!JSON.stringify(sanitized.data).includes('synthetic confidential account detail'));
});
