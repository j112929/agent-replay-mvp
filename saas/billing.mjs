import Stripe from 'stripe';
import { Problem, requireValue } from './domain.mjs';
import { audit } from './db.mjs';

export function billing(config, db, domain, provider) {
  const stripe = provider || (config.stripeKey ? new Stripe(config.stripeKey,{maxNetworkRetries:1,timeout:10000}) : null);
  let chain = Promise.resolve();
  const configured = () => Boolean(stripe && config.stripeWebhook && Object.values(config.prices).every(Boolean));
  async function customer(org) {
    const row = domain.entitlement(org);
    if (row.customer_id) return row.customer_id;
    const result = await stripe.customers.create({metadata:{guard_org_id:org}},{idempotencyKey:'guard-customer-'+org});
    requireValue(result.livemode === false,502,'Live billing is disabled');
    db.prepare('UPDATE guard_subscription SET customer_id=?,updated_at=? WHERE org_id=?').run(result.id,Date.now(),org);
    return result.id;
  }
  async function checkout(principal, org, plan) {
    domain.organization(principal,org,2);
    requireValue(configured(),503,'Stripe test billing is not configured');
    requireValue(['starter','team','business'].includes(plan) && config.prices[plan],400,'Invalid plan');
    const existing = domain.entitlement(org);
    const price = await stripe.prices.retrieve(config.prices[plan]);
    requireValue(price.livemode === false && price.active && price.currency === 'usd' && price.unit_amount === config.plans[plan].monthly_usd*100 && price.recurring?.interval === 'month' && price.recurring?.interval_count === 1,502,'Test price must match the configured USD monthly plan');
    requireValue(!existing.subscription_id || !['active','trialing','past_due','unpaid','incomplete'].includes(existing.status),409,'Manage an existing subscription in the billing portal');
    const result = await stripe.checkout.sessions.create({mode:'subscription',customer:await customer(org),
      client_reference_id:org, metadata:{guard_org_id:org}, subscription_data:{metadata:{guard_org_id:org}},
      line_items:[{price:config.prices[plan],quantity:1}],
      success_url:config.origin+'/debugger.html?billing=returned',cancel_url:config.origin+'/debugger.html?billing=cancelled'},
      {idempotencyKey:`guard-checkout-${org}-${plan}-${Math.floor(Date.now()/300000)}`});
    requireValue(result.livemode === false && /^https:\/\/checkout\.stripe\.com\//.test(result.url || ''),502,'Invalid test checkout response');
    audit(db,org,principal.user.id,'billing.checkout',plan); return {url:result.url,mode:'test'};
  }
  async function portal(principal, org) {
    domain.organization(principal,org,2);
    requireValue(configured(),503,'Stripe test billing is not configured');
    const ent = domain.entitlement(org);
    requireValue(ent.customer_id,409,'No subscription customer yet');
    const result = await stripe.billingPortal.sessions.create({customer:ent.customer_id,return_url:config.origin+'/debugger.html'});
    requireValue(/^https:\/\/billing\.stripe\.com\//.test(result.url || ''),502,'Invalid billing portal response');
    audit(db,org,principal.user.id,'billing.portal'); return {url:result.url};
  }
  async function processEvent(event) {
    requireValue(event && typeof event.id === 'string' && /^evt_[A-Za-z0-9_]{1,100}$/.test(event.id) && typeof event.type === 'string' && event.type.length < 100 && event.livemode === false,400,'Live or invalid billing events are rejected');
    const previous = db.prepare('SELECT status FROM guard_billing_event WHERE id=?').get(event.id);
    if (previous?.status === 'processed') return {received:true,duplicate:true};
    const supported = ['customer.subscription.created','customer.subscription.updated','customer.subscription.deleted'];
    db.prepare('INSERT OR IGNORE INTO guard_billing_event(id,type,status,created_at) VALUES(?,?,?,?)').run(event.id,event.type,'pending',Date.now());
    if (supported.includes(event.type)) {
      const ident = event.data?.object?.id;
      requireValue(typeof ident === 'string' && ident.startsWith('sub_'),400,'Invalid subscription event');
      // Fetch current state instead of trusting potentially stale, out-of-order event bodies.
      const subscription = await stripe.subscriptions.retrieve(ident);
      requireValue(subscription.livemode === false,400,'Live subscriptions are rejected');
      const customerId = typeof subscription.customer === 'string' ? subscription.customer : subscription.customer?.id;
      const row = db.prepare('SELECT * FROM guard_subscription WHERE customer_id=?').get(customerId);
      requireValue(row && subscription.metadata?.guard_org_id === row.org_id,400,'Subscription organization mismatch');
      if (row.subscription_id && row.subscription_id !== ident && !['canceled','incomplete_expired'].includes(row.status)) throw new Problem(409,'Unexpected subscription replacement');
      const items = subscription.items?.data || [];
      const item = items[0];
      const plan = Object.keys(config.prices).find(p => config.prices[p] === item?.price?.id);
      const valid = items.length === 1 && item.quantity === 1 && plan;
      const period = subscription.current_period_end || item?.current_period_end;
      const status = valid && Number.isFinite(period) ? subscription.status : 'invalid';
      db.prepare('UPDATE guard_subscription SET plan=?,status=?,subscription_id=?,period_end=?,cancel_at_period_end=?,updated_at=? WHERE org_id=?').run(valid ? plan : 'trial',status,ident,period ? period*1000 : null,subscription.cancel_at_period_end ? 1 : 0,Date.now(),row.org_id);
      db.prepare('UPDATE guard_billing_event SET org_id=? WHERE id=?').run(row.org_id,event.id);
      audit(db,row.org_id,'stripe','billing.reconcile',event.id);
    }
    db.prepare("UPDATE guard_billing_event SET status='processed',processed_at=? WHERE id=?").run(Date.now(),event.id);
    db.prepare("DELETE FROM guard_billing_event WHERE status='processed' AND created_at<?").run(Date.now()-90*86400000);
    return {received:true};
  }
  async function webhook(raw, signature) {
    requireValue(stripe && config.stripeWebhook,503,'Billing webhook not configured');
    let event;
    try { event = stripe.webhooks.constructEvent(raw,signature,config.stripeWebhook); }
    catch { throw new Problem(400,'Invalid billing webhook signature'); }
    const next = chain.then(() => processEvent(event));
    chain = next.catch(() => {}); return next;
  }
  return {configured,checkout,portal,webhook};
}
