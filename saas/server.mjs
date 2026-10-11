import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { join, basename } from 'node:path';
import { configuration } from './config.mjs';
import { database, migrate, audit, transaction } from './db.mjs';
import { domain, Problem, requireValue, identifier, text, digest } from './domain.mjs';
import { authentication } from './auth.mjs';
import { workers } from './worker.mjs';
import { billing } from './billing.mjs';
import { controllerLock } from './lock.mjs';

const publicRoot = fileURLToPath(new URL('../dist/',import.meta.url));
const types = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.zip':'application/zip'};
const extension = path => path.slice(path.lastIndexOf('.'));
const actions = {import:'imports',generate:'cases',evaluate:'runs'};
const authRoutes = new Set(['sign-up/email','sign-in/email','sign-out','get-session','list-sessions','revoke-session','revoke-sessions','revoke-other-sessions','change-password','update-user','send-verification-email','verify-email','request-password-reset','reset-password','error',
  'organization/create','organization/list','organization/update','organization/get-full-organization','organization/set-active','organization/get-active-member','organization/get-active-member-role','organization/list-members','organization/invite-member','organization/accept-invitation','organization/reject-invitation','organization/cancel-invitation','organization/get-invitation','organization/list-invitations','organization/list-user-invitations','organization/update-member-role','organization/remove-member','organization/leave']);

async function body(req, limit) {
  const declared = Number(req.headers['content-length']);
  requireValue(!Number.isFinite(declared) || declared <= limit,413,'Request too large');
  const chunks = []; let size = 0;
  for await (const chunk of req) { size += chunk.length; requireValue(size <= limit,413,'Request too large'); chunks.push(chunk); }
  return Buffer.concat(chunks);
}
function jsonBody(raw) {
  let value;
  try { value = JSON.parse(raw.toString('utf8')); } catch {throw new Problem(400,'Malformed JSON');}
  requireValue(value && typeof value === 'object' && !Array.isArray(value),400,'Expected a JSON object');
  function check(item, depth = 0) { requireValue(depth < 48,400,'JSON nesting exceeds limit'); if (item && typeof item === 'object') for (const child of Object.values(item)) check(child,depth+1); }
  check(value); return value;
}

export async function createApp({config = configuration(), provider, mailer, quiet = false} = {}) {
  const unlock = controllerLock(config.directory);
  let db,service,auth,outbox;
  try {
    db = database(config.directory);service = domain(config,db);
    ({auth,outbox} = await authentication(config,db,service.entitlement,mailer));
    migrate(db);
  } catch (error) {db?.close();unlock();throw error;}
  const worker = workers(config), subscriptions = billing(config,db,service,provider);
  let authQueue = Promise.resolve();
  let pendingAuth = 0;
  const rates = new Map();
  function rate(req, authRequest) {
    const now = Date.now(), key = req.socket.remoteAddress + (authRequest ? ':auth' : ':api');
    let entry = rates.get(key);
    if (!entry || now-entry.time > 60000) {entry = {time:now,count:0}; rates.set(key,entry);}
    requireValue(++entry.count <= (authRequest ? 40 : 180),429,'Request limit reached; retry in a minute');
    if (rates.size > 5000) for (const [id,item] of rates) if (now-item.time > 60000) rates.delete(id);
  }
  const send = (res,status,value,contentType = 'application/json') => {
    const data = contentType.startsWith('application/json') ? JSON.stringify(value) : value;
    res.writeHead(status,{'Content-Type':contentType+'; charset=utf-8','Content-Length':Buffer.byteLength(data),'Cache-Control':'no-store'});
    res.end(data);
  };
  async function operation(actor, project, action, value, req) {
    const kind = actions[action], org = project.org_id;
    const entitlement = service.entitlement(org);
    const sensitiveFields = JSON.parse(db.prepare('SELECT fields FROM guard_privacy WHERE org_id=?').get(org)?.fields || '[]');
    const cap = {imports:10000,cases:2000,runs:30000,storage_bytes:entitlement.limits.storage_bytes};
    if (!kind) {
      if (['approve','control'].includes(action)) requireValue(service.stored(org)+2048 <= entitlement.limits.storage_bytes,429,'Organization storage limit reached');
      if (action === 'approve') requireValue(entitlement.allowed,402,'Subscription inactive');
      const result = await worker.call(project,service.root(org,project.id),action,value,cap,sensitiveFields);
      audit(db,org,actor.user.id,action === 'report' ? 'report.export' : action === 'playback' ? 'replay.execute' : 'artifact.'+action,project.id);
      return result;
    }
    const key = req.headers['idempotency-key'];
    requireValue(typeof key === 'string' && /^[A-Za-z0-9_-]{8,100}$/.test(key),400,'Provide an Idempotency-Key (8–100 portable characters)');
    const hash = digest(JSON.stringify(value)), month = new Date().toISOString().slice(0,7);
    const reserve = action === 'evaluate' ? 4*1024*1024 : 3*1024*1024;
    const storedBefore = service.stored(org);
    const job = transaction(db,() => {
      // Keep a bounded idempotency ledger, not duplicate trace/fixture bodies in SQLite.
      db.prepare("DELETE FROM guard_job WHERE project_id=? AND status!='running' AND (created_at<? OR id NOT IN (SELECT id FROM guard_job WHERE project_id=? ORDER BY created_at DESC LIMIT 999))").run(project.id,Date.now()-7*86400000,project.id);
      const old = db.prepare('SELECT * FROM guard_job WHERE project_id=? AND request_key=?').get(project.id,key);
      if (old) {
        requireValue(old.body_hash === hash && old.action === action,409,'Idempotency key was used for a different request');
        requireValue(old.status === 'completed',409,'Job is pending, failed or interrupted; inspect it before retrying with a new key');
        return {...old,cached:true};
      }
      requireValue(entitlement.allowed,402,'Trial ended or subscription inactive; open Billing');
      const count = db.prepare('SELECT count FROM guard_usage WHERE org_id=? AND month=? AND kind=?').get(org,month,kind)?.count || 0;
      requireValue(count < entitlement.limits[kind],429,`Monthly ${kind} quota reached`);
      requireValue(!db.prepare("SELECT 1 FROM guard_job WHERE org_id=? AND status='running'").get(org),429,'An organization job is already running');
      requireValue(storedBefore+reserve <= entitlement.limits.storage_bytes,429,'Organization storage limit reached');
      const id = randomUUID(), now = Date.now();
      db.prepare("INSERT INTO guard_job VALUES(?,?,?,?,?,?,'running',NULL,?,?)").run(id,org,project.id,key,action,hash,now,now);
      db.prepare('INSERT INTO guard_usage VALUES(?,?,?,1) ON CONFLICT(org_id,month,kind) DO UPDATE SET count=count+1').run(org,month,kind);
      return {id};
    });
    if (job.cached) {
      const receipt = JSON.parse(job.result);
      if (action === 'generate') return worker.call(project,service.root(org,project.id),'case',{case_id:receipt.id,version:receipt.version},cap,sensitiveFields);
      if (action === 'evaluate') return worker.call(project,service.root(org,project.id),'run',{id:receipt.id},cap,sensitiveFields);
      return receipt;
    }
    try {
      let result = await worker.call(project,service.root(org,project.id),action,value,cap,sensitiveFields);
      if (action === 'import') result = {...result,privacy:Object.fromEntries(['status','redacted_field_count','replay_compatibility','limitations'].map(k=>[k,result.privacy[k]]))};
      const receipt = action === 'generate' ? {id:result.case.id,version:result.case.guard.version} : action === 'evaluate' ? {id:result.id} : result;
      transaction(db,() => {
        db.prepare("UPDATE guard_job SET status='completed',result=?,updated_at=? WHERE id=?").run(JSON.stringify(receipt),Date.now(),job.id);
        if (action === 'import' && result.duplicate) db.prepare('UPDATE guard_usage SET count=count-1 WHERE org_id=? AND month=? AND kind=?').run(org,month,kind);
        audit(db,org,actor.user.id,'guard.'+action,job.id);
      });
      return result;
    } catch (err) {
      transaction(db,() => {
        db.prepare("UPDATE guard_job SET status='failed',result=NULL,updated_at=? WHERE id=?").run(Date.now(),job.id);
        // Refund only when no artifacts were written. Interrupted partial work stays charged.
        if (service.stored(org) === storedBefore) db.prepare('UPDATE guard_usage SET count=count-1 WHERE org_id=? AND month=? AND kind=?').run(org,month,kind);
        audit(db,org,actor.user.id,'guard.'+action+'.rejected',job.id);
      });
      throw err;
    }
  }
  async function handle(req,res) {
    const expected = new URL(config.origin);
    requireValue(req.headers.host === expected.host,403,'Host is not allowed');
    if (req.headers.origin) requireValue(req.headers.origin === config.origin,403,'Origin is not allowed');
    const url = new URL(req.url,config.origin), path = url.pathname;
    if (path.startsWith('/api/')) rate(req,path.startsWith('/api/auth/'));
    if (path === '/api/health') return send(res,200,{service:'agent-regression-guard',schema:1,status:'ok',active_jobs:worker.active,billing_mode:'test'});
    if (path === '/api/saas/config' && req.method === 'GET') return send(res,200,{service:'agent-regression-guard',development:!config.production,plans:config.plans,billing_mode:'test',billing_configured:subscriptions.configured(),trial_days:config.trialDays,replay_mode:'RECORDED_REPLAY'});
    if (path === '/api/billing/webhook' && req.method === 'POST') return send(res,200,await subscriptions.webhook(await body(req,512*1024),req.headers['stripe-signature']));
    if (path.startsWith('/api/auth/')) {
      requireValue(!['/api/auth/delete-user','/api/auth/organization/delete'].includes(path),403,'Account deletion requires the documented retention procedure');
      requireValue(authRoutes.has(path.slice('/api/auth/'.length)) || /^\/api\/auth\/reset-password\/[A-Za-z0-9_-]{8,200}$/.test(path),404,'Auth route not exposed');
      const raw = ['GET','HEAD'].includes(req.method) ? undefined : await body(req,16384);
      if (raw) {
        requireValue(req.headers.origin === config.origin,403,'Account mutations require the same Origin');
        requireValue(req.headers['content-type']?.split(';')[0] === 'application/json',415,'Use application/json');
        jsonBody(raw);
      }
      requireValue(pendingAuth < 16,429,'Authentication queue is full; retry shortly');
      const headers = new Headers();
      for (const [name,value] of Object.entries(req.headers)) if (typeof value === 'string' && !name.startsWith('x-forwarded-')) headers.set(name,value);
      const webRequest = new Request(url,{method:req.method,headers,body:raw});
      const auditOperation = {'/api/auth/organization/remove-member':'member.remove','/api/auth/organization/leave':'member.leave','/api/auth/organization/update-member-role':'member.role','/api/auth/organization/cancel-invitation':'invitation.cancel','/api/auth/organization/update':'organization.update'}[path];
      // Organization hooks expose the affected user, not the acting admin.
      const actingSession = auditOperation ? await auth.api.getSession({headers}) : null;
      let auditOrg;
      if (auditOperation && raw) {try {auditOrg=JSON.parse(raw).organizationId || actingSession?.session.activeOrganizationId;}catch{}}
      pendingAuth++;
      const next = authQueue.then(() => auth.handler(webRequest)).finally(() => pendingAuth--);
      authQueue = next.catch(() => {});
      const response = await next;
      if (response.ok && actingSession && auditOperation === 'member.leave' && auditOrg) {
        db.prepare('UPDATE guard_key SET revoked_at=? WHERE org_id=? AND user_id=?').run(Date.now(),auditOrg,actingSession.user.id);
        db.prepare('DELETE FROM guard_access WHERE user_id=? AND project_id IN (SELECT id FROM guard_project WHERE org_id=?)').run(actingSession.user.id,auditOrg);
      }
      if (response.ok && actingSession) audit(db,typeof auditOrg === 'string' ? auditOrg : null,actingSession.user.id,auditOperation);
      for (const [name,value] of response.headers) if (name !== 'set-cookie') res.setHeader(name,value);
      const cookies = response.headers.getSetCookie(); if (cookies.length) res.setHeader('Set-Cookie',cookies);
      res.statusCode = response.status; res.end(Buffer.from(await response.arrayBuffer())); return;
    }
    if (!path.startsWith('/api/')) {
      requireValue(['GET','HEAD'].includes(req.method),405,'Method not allowed');
      const name = path === '/' ? 'debugger.html' : path.slice(1);
      requireValue(name === basename(name) && /^[A-Za-z0-9_.-]+$/.test(name) && Object.hasOwn(types,extension(name)),404,'Page not found');
      const target = join(publicRoot,name);
      try {
        const size = (await stat(target)).size;
        res.writeHead(200,{'Content-Type':types[extension(name)],'Content-Length':size,'Cache-Control':'no-cache'});
        res.end(req.method === 'HEAD' ? undefined : await readFile(target)); return;
      } catch { throw new Problem(404,'Page not found'); }
    }
    const actor = await service.principal(req,auth);
    let value = {};
    if (!['GET','HEAD'].includes(req.method)) {
      if (!actor.key) requireValue(req.headers.origin === config.origin,403,'Account mutations require the same Origin');
      requireValue(req.headers['content-type']?.split(';')[0] === 'application/json',415,'Use application/json');
      value = jsonBody(await body(req,1024*1024+65536));
    }
    if (path === '/api/saas/me' && req.method === 'GET') {
      requireValue(!actor.key,403,'Account session required');
      const orgs = db.prepare('SELECT o.id,o.name,o.slug,m.role FROM organization o JOIN member m ON m.organizationId=o.id WHERE m.userId=?').all(actor.user.id);
      const invitations = db.prepare("SELECT i.id,i.organizationId,i.role,o.name FROM invitation i JOIN organization o ON o.id=i.organizationId WHERE lower(i.email)=lower(?) AND i.status='pending' AND i.expiresAt>?").all(actor.user.email,Date.now());
      return send(res,200,{user:{id:actor.user.id,name:actor.user.name,email:actor.user.email,emailVerified:actor.user.emailVerified},organizations:orgs,invitations});
    }
    const orgRoute = path.match(/^\/api\/saas\/organizations\/([A-Za-z0-9_-]+)\/(projects|team|usage|billing|audit|checkout|portal|privacy)$/);
    if (orgRoute) {
      const [,org,resource] = orgRoute;
      service.organization(actor,org,['audit','checkout','portal'].includes(resource) ? 2 : 0);
      if (resource === 'privacy' && req.method === 'GET') return send(res,200,{sensitive_fields:JSON.parse(db.prepare('SELECT fields FROM guard_privacy WHERE org_id=?').get(org)?.fields || '[]'),required_credentials_redaction:true});
      if (resource === 'privacy' && req.method === 'POST') {
        service.organization(actor,org,2);
        requireValue(Array.isArray(value.sensitive_fields) && value.sensitive_fields.length <= 50 && value.sensitive_fields.every(s => typeof s === 'string' && /^[A-Za-z_][A-Za-z0-9_.-]{0,79}$/.test(s)),400,'Provide up to 50 structured sensitive field names');
        db.prepare('INSERT INTO guard_privacy VALUES(?,?,?) ON CONFLICT(org_id) DO UPDATE SET fields=excluded.fields,updated_at=excluded.updated_at').run(org,JSON.stringify([...new Set(value.sensitive_fields)]),Date.now());
        audit(db,org,actor.user.id,'privacy.policy');return send(res,200,{updated:true});
      }
      if (resource === 'projects' && req.method === 'GET') {
        const role = service.membership(actor.user.id,org).role;
        const rows = service.level[role] >= 2 ? db.prepare('SELECT * FROM guard_project WHERE org_id=?').all(org) : db.prepare('SELECT p.* FROM guard_project p JOIN guard_access a ON a.project_id=p.id WHERE p.org_id=? AND a.user_id=?').all(org,actor.user.id);
        return send(res,200,{items:rows});
      }
      if (resource === 'projects' && req.method === 'POST') return send(res,201,service.createProject(actor,org,value.name));
      if (resource === 'usage' && req.method === 'GET') return send(res,200,service.usage(org));
      if (resource === 'billing' && req.method === 'GET') return send(res,200,{subscription:service.entitlement(org),configured:subscriptions.configured(),mode:'test'});
      if (resource === 'checkout' && req.method === 'POST') return send(res,200,await subscriptions.checkout(actor,org,value.plan));
      if (resource === 'portal' && req.method === 'POST') return send(res,200,await subscriptions.portal(actor,org));
      if (resource === 'audit' && req.method === 'GET') return send(res,200,{items:db.prepare('SELECT * FROM guard_audit WHERE org_id=? ORDER BY created_at DESC LIMIT 100').all(org)});
      if (resource === 'team' && req.method === 'GET') {
        const members = db.prepare('SELECT m.id,m.userId,m.role,u.name,u.email FROM member m JOIN user u ON u.id=m.userId WHERE m.organizationId=?').all(org);
        const role = service.membership(actor.user.id,org).role;
        return send(res,200,{members,invitations:service.level[role] >= 2 ? db.prepare("SELECT id,email,role,status FROM invitation WHERE organizationId=? AND status='pending'").all(org) : []});
      }
    }
    const projectRoute = path.match(/^\/api\/saas\/projects\/([A-Za-z0-9_-]+)\/(.+)$/);
    if (projectRoute) {
      const [,projectId,resource] = projectRoute, mutation = req.method === 'POST';
      const scope = resource === 'import' ? 'import' : resource === 'evaluate' || resource.endsWith('/execute') ? 'run' : 'read';
      const project = service.project(actor,projectId,mutation ? 1 : 0,scope);
      const org = project.org_id;
      if (resource === 'access' && req.method === 'GET') {
        requireValue(!actor.key,403,'Account session required');
        return send(res,200,{items:db.prepare('SELECT user_id FROM guard_access WHERE project_id=?').all(projectId)});
      }
      if (resource === 'access' && mutation) {
        service.organization(actor,org,2); service.membership(identifier(value.user_id),org);
        requireValue(typeof value.enabled === 'boolean',400,'Specify enabled');
        if (value.enabled) db.prepare('INSERT OR IGNORE INTO guard_access VALUES(?,?)').run(projectId,value.user_id);
        else db.prepare('DELETE FROM guard_access WHERE project_id=? AND user_id=?').run(projectId,value.user_id);
        audit(db,org,actor.user.id,'project.access',projectId); return send(res,200,{updated:true});
      }
      if (resource === 'keys' && req.method === 'GET') {
        requireValue(!actor.key,403,'Account session required');
        return send(res,200,{items:db.prepare('SELECT id,user_id,name,prefix,scopes,created_at,expires_at,last_used,revoked_at FROM guard_key WHERE project_id=? AND (user_id=? OR ?=1)').all(projectId,actor.user.id,service.level[service.membership(actor.user.id,org).role] >= 2 ? 1 : 0)});
      }
      if (resource === 'keys' && mutation) return send(res,201,service.createKey(actor,projectId,value));
      const keyAction = resource.match(/^keys\/([A-Za-z0-9_-]+)\/(revoke|rotate)$/);
      if (keyAction && mutation) {
        requireValue(!actor.key,403,'Account session required');
        const key = db.prepare('SELECT * FROM guard_key WHERE id=? AND project_id=?').get(keyAction[1],projectId);
        requireValue(key && (key.user_id === actor.user.id || service.level[service.membership(actor.user.id,org).role] >= 2),403,'Key access denied');
        if (keyAction[2] === 'rotate') return send(res,201,service.createKey(actor,projectId,{...value,name:value.name || key.name,scopes:JSON.parse(key.scopes)},key.id));
        db.prepare('UPDATE guard_key SET revoked_at=? WHERE id=?').run(Date.now(),key.id); audit(db,org,actor.user.id,'key.revoke',key.id); return send(res,200,{revoked:true});
      }
      if (resource === 'jobs' && req.method === 'GET') return send(res,200,{items:db.prepare('SELECT id,action,status,created_at,updated_at FROM guard_job WHERE project_id=? ORDER BY created_at DESC LIMIT 100').all(projectId)});
      if (resource === 'suites' && req.method === 'GET') return send(res,200,{items:db.prepare('SELECT * FROM guard_suite WHERE project_id=?').all(projectId).map(x => ({...x,cases:JSON.parse(x.cases)}))});
      if (resource === 'suites' && mutation) {
        requireValue(!actor.key,403,'Review suites using an account session');
        requireValue(service.entitlement(org).allowed,402,'Subscription inactive');
        requireValue(Array.isArray(value.cases) && value.cases.length > 0 && value.cases.length <= 25 && value.cases.every(c => typeof c === 'object' && !Array.isArray(c) && /^[a-f0-9-]{36}$/.test(c.id) && Number.isInteger(c.version) && c.version > 0),400,'Select 1–25 approved case versions');
        for (const item of value.cases) {const data = await operation(actor,project,'case',{case_id:item.id,version:item.version},req); requireValue(data.review.state === 'approved',422,'Suites require approved case versions');}
        requireValue(db.prepare('SELECT count(*) n FROM guard_suite WHERE project_id=?').get(projectId).n < 30,429,'Suite limit reached');
        const id = randomUUID(), name = text(value.name);
        db.prepare('INSERT INTO guard_suite VALUES(?,?,?,?,?)').run(id,projectId,name,JSON.stringify(value.cases),Date.now());
        audit(db,org,actor.user.id,'suite.create',id); return send(res,201,{id,name,cases:value.cases});
      }
      const suiteExecute = resource.match(/^suites\/([a-f0-9-]{36})\/execute$/);
      if (suiteExecute && mutation) {
        const suite = db.prepare('SELECT * FROM guard_suite WHERE id=? AND project_id=?').get(suiteExecute[1],projectId);
        requireValue(suite,404,'Suite not found');
        const baseKey = req.headers['idempotency-key'];
        requireValue(typeof baseKey === 'string' && /^[A-Za-z0-9_-]{8,60}$/.test(baseKey),400,'Provide an Idempotency-Key');
        requireValue(value.evidence && typeof value.evidence === 'object',400,'Provide evidence by case ID');
        const results = [];
        for (const item of JSON.parse(suite.cases)) {
          const evidence = value.evidence[item.id] || {};
          const result = await operation(actor,project,'evaluate',{case_id:item.id,version:item.version,baseline:evidence.baseline,candidate:evidence.candidate},{headers:{'idempotency-key':baseKey+'_'+item.id}});
          results.push(result);
        }
        const decision = results.some(r => r.decision === 'FAIL') ? 'FAIL' : results.some(r => r.decision !== 'PASS') ? 'INCONCLUSIVE' : 'PASS';
        return send(res,200,{suite_id:suite.id,decision,blocking:decision !== 'PASS',exit_code:{PASS:0,FAIL:1,INCONCLUSIVE:3}[decision],results});
      }
      if (['import','generate','approve','control','evaluate'].includes(resource) && mutation) {
        if (['generate','approve','control'].includes(resource)) requireValue(!actor.key,403,'Verifier generation and approval require an account session');
        if (['approve','control'].includes(resource)) service.project(actor,projectId,2);
        if (['approve','control'].includes(resource)) value.reviewer = actor.user.id; // Never trust client-supplied reviewer identity.
        return send(res,200,await operation(actor,project,resource,value,req));
      }
      if (['traces','cases','runs','usage'].includes(resource) && req.method === 'GET') return send(res,200,await operation(actor,project,resource,{},req));
      const detail = resource.match(/^(traces|cases|runs)\/([A-Za-z0-9_-]+)(\/(playback|report))?$/);
      if (detail && req.method === 'GET') {
        const action = detail[4] || {traces:'trace',cases:'case',runs:'run'}[detail[1]];
        requireValue(!detail[4] || (detail[4] === 'playback' && detail[1] === 'traces') || (detail[4] === 'report' && detail[1] === 'runs'),404,'Unknown artifact');
        const data = await operation(actor,project,action,action === 'case' ? {case_id:detail[2]} : {id:detail[2]},req);
        return send(res,200,data);
      }
    }
    throw new Problem(404,'API route not found');
  }
  const server = http.createServer(async (req,res) => {
    const requestId = randomUUID(), start = Date.now();
    res.setHeader('X-Request-ID',requestId); res.setHeader('X-Content-Type-Options','nosniff');
    res.setHeader('Referrer-Policy','no-referrer'); res.setHeader('X-Frame-Options','SAMEORIGIN');
    res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'");
    if (config.production) res.setHeader('Strict-Transport-Security','max-age=31536000');
    try { await handle(req,res); }
    catch (err) { if (!res.headersSent) send(res,err instanceof Problem ? err.status : 500,{error:err instanceof Problem ? err.message : 'Request failed; contact the operator with the request ID',request_id:requestId}); else res.end(); }
    if (!quiet) console.log(JSON.stringify({event:'http',request_id:requestId,method:req.method,status:res.statusCode,duration_ms:Date.now()-start}));
  });
  server.requestTimeout = 30000; server.headersTimeout = 15000; server.maxRequestsPerSocket = 200;
  return {server,db,auth,service,subscriptions,outbox,config,close:async () => {
    await new Promise(resolve => server.close(resolve));
    server.closeAllConnections();
    db.close();unlock();
  }};
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const app = await createApp();
  app.server.listen(app.config.port,app.config.host,() => console.log(JSON.stringify({event:'ready',origin:app.config.origin,billing:'test',development:!app.config.production})));
  const shutdown = async () => {await app.close(); process.exit(0);};
  process.on('SIGTERM',shutdown); process.on('SIGINT',shutdown);
}
