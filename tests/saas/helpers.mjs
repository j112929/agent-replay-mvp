import http from 'node:http';
import {mkdtemp, rm, readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import Stripe from 'stripe';
import {configuration} from '../../saas/config.mjs';
import {createApp} from '../../saas/server.mjs';

export const samples = JSON.parse(await readFile(new URL('../../dist/saas-demo.json',import.meta.url),'utf8'));
export class FakeStripe {
  constructor(){
    this.state = null;this.calls=[];
    this.webhooks= new Stripe('sk_test_synthetic').webhooks;
    this.customers={create:async () => ({id:'cus_synthetic',livemode:false})};
    this.prices={retrieve:async id=>({id,livemode:false,active:true,currency:'usd',unit_amount:{price_starter:29900,price_team:79900,price_business:199900}[id],recurring:{interval:'month',interval_count:1}})};
    this.checkout={sessions:{create:async data => {this.calls.push(data);return {url:'https://checkout.stripe.com/c/test_synthetic',livemode:false};}}};
    this.billingPortal={sessions:{create:async () => ({url:'https://billing.stripe.com/p/session/test_synthetic'})}};
    this.subscriptions={retrieve:async () => structuredClone(this.state)};
  }
}
export async function fixture(overrides = {}) {
  const probe=http.createServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));const port=probe.address().port;await new Promise(r=>probe.close(r));
  const directory=await mkdtemp(join(tmpdir(),'guard-saas-'));
  const config=configuration({GUARD_ORIGIN:`http://127.0.0.1:${port}`,GUARD_DATA_DIR:directory,STRIPE_WEBHOOK_SECRET:'whsec_synthetic',STRIPE_PRICE_STARTER:'price_starter',STRIPE_PRICE_TEAM:'price_team',STRIPE_PRICE_BUSINESS:'price_business'});
  Object.assign(config,overrides);
  const provider=new FakeStripe(), messages=[];
  const app=await createApp({config,provider,mailer:async message=>messages.push(message),quiet:true});
  await new Promise(r=>app.server.listen(port,'127.0.0.1',r));
  const request=async (path,value,credential={},extra={}) => {
    const headers={...credential,Origin:config.origin,Connection:'close',...extra};
    if(value!==undefined){headers['Content-Type']='application/json';headers['Idempotency-Key']=extra['Idempotency-Key'] || randomUUID();}
    const r=await fetch(config.origin+path,{method:value===undefined?'GET':'POST',headers,body:value===undefined?undefined:JSON.stringify(value),redirect:'manual'});
    return {status:r.status,data:await r.json().catch(()=>({})),cookies:r.headers.getSetCookie(),response:r};
  };
  const signup=async (email='owner@example.invalid') => {const r=await request('/api/auth/sign-up/email',{name:email.split('@')[0]+' Engineer',email,password:'synthetic-demo-password-2026'});if(r.status!==200)throw new Error(JSON.stringify(r.data));return {Cookie:r.cookies.map(x=>x.split(';')[0]).join('; ')};};
  const org=async credential => (await request('/api/auth/organization/create',{name:'Synthetic Team',slug:'team-'+randomUUID().slice(0,8)},credential)).data;
  const project=async (credential,orgId) => (await request('/api/saas/organizations/'+orgId+'/projects',{name:'Coding agent'},credential)).data;
  const stop=async()=>{await app.close();await rm(directory,{recursive:true,force:true});};
  return {app,config,provider,messages,request,signup,org,project,stop};
}
