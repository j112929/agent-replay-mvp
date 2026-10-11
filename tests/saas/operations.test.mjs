import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './helpers.mjs';
import {createApp} from '../../saas/server.mjs';

test('one controller per directory and additive migrations preserve accounts and ownership',async t=>{
  const f=await fixture();const owner=await f.signup(),org=await f.org(owner),p=await f.project(owner,org.id);
  await assert.rejects(createApp({config:f.config,quiet:true}),/already using/);
  await f.app.close();
  const restarted=await createApp({config:f.config,quiet:true});
  await new Promise(r=>restarted.server.listen(f.config.port,'127.0.0.1',r));
  t.after(async()=>{await restarted.close(); const {rm}=await import('node:fs/promises');await rm(f.config.directory,{recursive:true,force:true});});
  assert.equal((await f.request('/api/saas/me',undefined,owner)).status,200);
  assert.equal(restarted.db.prepare('SELECT org_id FROM guard_project WHERE id=?').get(p.id).org_id,org.id);
  assert.equal(restarted.db.prepare('SELECT max(version) version FROM guard_schema').get().version,1);
});

test('server exposes no credentials, raw logs, arbitrary files or execution endpoints',async t=>{
  const f=await fixture();t.after(f.stop);
  const publicConfig=(await f.request('/api/saas/config')).data;
  assert.ok(!JSON.stringify(publicConfig).includes(f.config.secret));
  assert.ok(!JSON.stringify(publicConfig).includes(f.config.stripeWebhook));
  assert.equal((await f.request('/api/saas/me')).status,401);
  const owner=await f.signup(),org=await f.org(owner),p=await f.project(owner,org.id);
  assert.equal((await f.request('/api/saas/projects/'+p.id+'/shell',{command:'touch /tmp/unsafe'},owner)).status,404);
  assert.equal((await f.request('/accounts.sqlite',undefined)).status,404);
  const worker=await f.request('/api/saas/projects/'+p.id+'/evaluate',{case_id:'../../accounts.sqlite'},owner);assert.equal(worker.status,422);
});
