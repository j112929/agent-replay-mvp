import { randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

function url(value, label) {try {return new URL(value);} catch {throw new Error('Invalid '+label);}}

export function configuration(env = process.env) {
  const production = env.NODE_ENV === 'production';
  const origin = url(env.GUARD_ORIGIN || 'http://127.0.0.1:3000','GUARD_ORIGIN');
  if (!['http:','https:'].includes(origin.protocol)) throw new Error('Use an HTTP(S) origin');
  if (origin.pathname !== '/' || origin.search || origin.hash || origin.username || origin.password) throw new Error('GUARD_ORIGIN must be an origin');
  if (production && origin.protocol !== 'https:') throw new Error('Production requires HTTPS');
  if (!production && !['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname)) throw new Error('Development is loopback-only');
  const secret = env.BETTER_AUTH_SECRET || (!production && randomBytes(48).toString('base64url'));
  if (!secret || secret.length < 48) throw new Error('Set BETTER_AUTH_SECRET to at least 48 random characters');
  if (production && (!env.GUARD_SMTP_URL || !env.GUARD_MAIL_FROM)) throw new Error('Production requires SMTP and GUARD_MAIL_FROM');
  if (env.GUARD_SMTP_URL) {
    const smtp = url(env.GUARD_SMTP_URL,'GUARD_SMTP_URL');
    if (!['smtp:','smtps:'].includes(smtp.protocol) || smtp.search) throw new Error('Use an SMTP(S) URL without unsafe transport overrides');
  }
  if (env.STRIPE_SECRET_KEY && !env.STRIPE_SECRET_KEY.startsWith('sk_test_')) throw new Error('Only Stripe test keys are supported');
  let plans;
  try {plans = JSON.parse(readFileSync(env.GUARD_PLANS_FILE || new URL('./plans.json', import.meta.url), 'utf8'));}
  catch {throw new Error('Plan configuration could not be loaded');}
  for (const name of ['trial', 'starter', 'team', 'business']) {
    if (!plans[name] || !Number.isFinite(plans[name].monthly_usd)) throw new Error('Invalid plan configuration');
    for (const key of ['imports','cases','runs','storage_bytes','projects','seats']) if (!Number.isSafeInteger(plans[name][key]) || plans[name][key] < 1) throw new Error('Invalid plan limits');
  }
  const port = Number(env.PORT || origin.port || 3000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid server port');
  return { production, origin: origin.origin, secret, host: production ? '0.0.0.0' : '127.0.0.1', port,
    directory: resolve(env.GUARD_DATA_DIR || '.replay/saas'), python: env.GUARD_PYTHON || 'python', plans,
    smtp: env.GUARD_SMTP_URL, mailFrom: env.GUARD_MAIL_FROM || 'Agent Regression Guard <guard@localhost>',
    stripeKey: env.STRIPE_SECRET_KEY, stripeWebhook: env.STRIPE_WEBHOOK_SECRET,
    prices: Object.fromEntries(['starter','team','business'].map(p => [p, env[`STRIPE_PRICE_${p.toUpperCase()}`]])),
    trialDays: 14, maxJobs: 2, workerTimeout: 20000, sessionDays: 7 };
}
