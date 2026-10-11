import {test} from 'node:test';
import assert from 'node:assert/strict';
import {configuration} from '../../saas/config.mjs';
import {fixture} from './helpers.mjs';

test('signup, hashed passwords, session, CSRF and production configuration',async t=>{
  const f=await fixture();t.after(f.stop);
  const user=await f.signup(), me=await f.request('/api/saas/me',undefined,user);
  assert.equal(me.status,200);assert.equal(me.data.user.email,'owner@example.invalid');
  const password=f.app.db.prepare('SELECT password FROM account').get().password;
  assert.notEqual(password,'synthetic-demo-password-2026');
  const o=await f.org(user);
  assert.equal((await f.request('/api/saas/organizations/'+o.id+'/projects',{name:'Bad origin'},user,{Origin:'https://attacker.invalid'})).status,403);
  assert.equal((await f.request('/api/auth/sign-in/email',{email:me.data.user.email,password:'wrong-password-value'})).status,401);
  assert.equal((await f.request('/api/auth/sign-out',{},user)).status,200);
  assert.equal((await f.request('/api/saas/me',undefined,user)).status,401);
  assert.throws(()=>configuration({NODE_ENV:'production',GUARD_ORIGIN:'http://example.invalid'}),/HTTPS/);
  assert.throws(()=>configuration({STRIPE_SECRET_KEY:'sk_live_synthetic'}),/test keys/);
  assert.throws(()=>configuration({NODE_ENV:'production',GUARD_ORIGIN:'https://example.invalid',BETTER_AUTH_SECRET:'x'.repeat(48)}),/SMTP/);
});

test('organization and project isolation, invite roles and current key permissions',async t=>{
  const f=await fixture();t.after(f.stop);
  const owner=await f.signup(), org=await f.org(owner), p=await f.project(owner,org.id), base='/api/saas/projects/'+p.id;
  const outsider=await f.signup('outsider@example.invalid');
  assert.equal((await f.request(base+'/traces',undefined,outsider)).status,403);
  assert.equal((await f.request('/api/saas/organizations/'+org.id+'/usage',undefined,outsider)).status,403);
  const viewer=await f.signup('viewer@example.invalid');
  const invite=await f.request('/api/auth/organization/invite-member',{email:'viewer@example.invalid',role:'viewer',organizationId:org.id},owner);
  assert.equal(invite.status,200);
  assert.equal((await f.request('/api/auth/organization/accept-invitation',{invitationId:invite.data.id},viewer)).status,200);
  assert.equal((await f.request(base+'/traces',undefined,viewer)).status,403);
  const userId=(await f.request('/api/saas/me',undefined,viewer)).data.user.id;
  assert.equal((await f.request(base+'/access',{user_id:userId,enabled:true},owner)).status,200);
  assert.equal((await f.request(base+'/traces',undefined,viewer)).status,200);
  assert.equal((await f.request(base+'/keys',{name:'Blocked key'},viewer)).status,403);
  assert.equal((await f.request('/api/auth/organization/invite-member',{email:'x@example.invalid',role:'admin',organizationId:org.id},viewer)).status,403);
  const member=f.app.db.prepare('SELECT * FROM member WHERE userId=? AND organizationId=?').get(userId,org.id);
  assert.equal((await f.request('/api/auth/organization/update-member-role',{memberId:member.id,role:'member',organizationId:org.id},owner)).status,200);
  const key=(await f.request(base+'/keys',{name:'CI key'},viewer)).data;
  assert.match(key.key,/^arg_/);assert.notEqual(f.app.db.prepare('SELECT hash FROM guard_key WHERE id=?').get(key.id).hash,key.key);
  const bearer={Authorization:'Bearer '+key.key};
  assert.equal((await f.request(base+'/traces',undefined,bearer)).status,200);
  assert.equal((await f.request(base+'/generate',{source:'missing'},bearer)).status,403);
  const p2=await f.project(owner,org.id);
  assert.equal((await f.request('/api/saas/projects/'+p2.id+'/traces',undefined,bearer)).status,403);
  await f.request('/api/auth/organization/update-member-role',{memberId:member.id,role:'viewer',organizationId:org.id},owner);
  assert.equal((await f.request(base+'/import',{trace:{}},bearer)).status,403);
  const rotated=(await f.request(base+'/keys/'+key.id+'/rotate',{},owner)).data;
  assert.equal((await f.request(base+'/traces',undefined,bearer)).status,401);
  assert.equal((await f.request(base+'/traces',undefined,{Authorization:'Bearer '+rotated.key})).status,200);
  await f.request(base+'/keys/'+rotated.id+'/revoke',{},owner);
  assert.equal((await f.request(base+'/traces',undefined,{Authorization:'Bearer '+rotated.key})).status,401);
});

test('pending invitations reserve seats and trials fail closed',async t=>{
  const f=await fixture();t.after(f.stop);const owner=await f.signup(), org=await f.org(owner);
  for(const email of ['a','b'])assert.equal((await f.request('/api/auth/organization/invite-member',{email:email+'@example.invalid',role:'member',organizationId:org.id},owner)).status,200);
  assert.equal((await f.request('/api/auth/organization/invite-member',{email:'c@example.invalid',role:'viewer',organizationId:org.id},owner)).status,403);
  f.app.db.prepare('UPDATE guard_subscription SET trial_ends=0 WHERE org_id=?').run(org.id);
  assert.equal((await f.request('/api/saas/organizations/'+org.id+'/projects',{name:'Blocked project'},owner)).status,402);
});

test('password reset and verification use private mail and revoke old sessions',async t=>{
  const f=await fixture();t.after(f.stop);const owner=await f.signup();
  const verification=f.messages.find(m=>m.subject.includes('Verify'));
  assert.ok(verification.text.includes('/verify-email'));
  const verify=await fetch(verification.text,{redirect:'manual'});assert.ok([200,302].includes(verify.status));
  assert.equal(f.app.db.prepare('SELECT emailVerified FROM user').get().emailVerified,1);
  await f.request('/api/auth/request-password-reset',{email:'owner@example.invalid',redirectTo:f.config.origin+'/debugger.html?screen=reset'});
  const reset=f.messages.find(m=>m.subject.includes('Reset'));
  assert.ok(reset);const token=new URL(reset.text).pathname.split('/').at(-1);
  const r=await f.request('/api/auth/reset-password',{token,newPassword:'new-synthetic-password-2026'});
  assert.equal(r.status,200);assert.equal((await f.request('/api/saas/me',undefined,owner)).status,401);
});
