import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fixture} from './helpers.mjs';

test('test Checkout and Portal require admin; webhook signature, duplicates and current provider state',async t=>{
  const f=await fixture();t.after(f.stop);const owner=await f.signup(), org=await f.org(owner), base='/api/saas/organizations/'+org.id;
  const checkout=await f.request(base+'/checkout',{plan:'team'},owner);
  assert.equal(checkout.status,200);assert.equal(checkout.data.mode,'test');
  assert.equal(f.app.service.entitlement(org.id).plan,'trial'); // Redirects never grant a paid entitlement.
  assert.equal(f.provider.calls[0].subscription_data.metadata.guard_org_id,org.id);
  assert.equal((await f.request(base+'/checkout',{plan:'attacker-price'},owner)).status,400);
  const retrievePrice=f.provider.prices.retrieve;
  f.provider.prices.retrieve=async id=>({...await retrievePrice(id),unit_amount:1});
  assert.equal((await f.request(base+'/checkout',{plan:'starter'},owner)).status,502);
  f.provider.prices.retrieve=retrievePrice;
  assert.equal((await f.request(base+'/portal',{},owner)).status,200);
  const subscription={id:'sub_synthetic',customer:'cus_synthetic',livemode:false,metadata:{guard_org_id:org.id},status:'active',items:{data:[{price:{id:'price_team'},quantity:1,current_period_end:Math.floor(Date.now()/1000)+86400}]},cancel_at_period_end:false};
  f.provider.state=subscription;
  async function webhook(event, valid = true) {
    const payload=JSON.stringify(event), signature=f.provider.webhooks.generateTestHeaderString({payload,secret:valid?f.config.stripeWebhook:'whsec_wrong'});
    const r=await fetch(f.config.origin+'/api/billing/webhook',{method:'POST',headers:{'Content-Type':'application/json','Stripe-Signature':signature},body:payload});
    return {status:r.status,data:await r.json()};
  }
  const event={id:'evt_first',type:'customer.subscription.updated',livemode:false,data:{object:{id:'sub_synthetic',status:'past_due'}}};
  assert.equal((await webhook(event,false)).status,400);
  assert.equal((await webhook(event)).status,200);
  let ent=f.app.service.entitlement(org.id);assert.equal(ent.plan,'team');assert.equal(ent.allowed,true);
  assert.equal((await webhook(event)).data.duplicate,true);
  f.provider.state={...subscription,status:'canceled'};
  assert.equal((await webhook({...event,id:'evt_old',type:'customer.subscription.created'})).status,200);
  assert.equal(f.app.service.entitlement(org.id).allowed,false);
  assert.equal((await webhook({...event,id:'evt_live',livemode:true})).status,400);
  f.provider.state={...subscription,items:{data:[{price:{id:'price_unknown'},quantity:1,current_period_end:Math.floor(Date.now()/1000)+86400}]}};
  assert.equal((await webhook({...event,id:'evt_unknown'})).status,200);
  assert.equal(f.app.service.entitlement(org.id).allowed,false);
  assert.ok(f.app.db.prepare("SELECT count(*) n FROM guard_audit WHERE operation='billing.reconcile'").get().n >= 3);
});

test('foreign subscriptions and malformed signed events cannot grant access',async t=>{
  const f=await fixture();t.after(f.stop);const owner=await f.signup(), org=await f.org(owner);
  await f.request('/api/saas/organizations/'+org.id+'/checkout',{plan:'team'},owner);
  f.provider.state={id:'sub_wrong',livemode:false,customer:'cus_synthetic',metadata:{guard_org_id:'foreign-org'},status:'active'};
  const payload=JSON.stringify({id:'evt_foreign',type:'customer.subscription.updated',livemode:false,data:{object:{id:'sub_wrong'}}});
  const signature=f.provider.webhooks.generateTestHeaderString({payload,secret:f.config.stripeWebhook});
  const r=await fetch(f.config.origin+'/api/billing/webhook',{method:'POST',headers:{'Stripe-Signature':signature},body:payload});
  assert.equal(r.status,400);assert.equal(f.app.service.entitlement(org.id).plan,'trial');
});
