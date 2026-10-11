import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { existsSync, readdirSync, lstatSync } from 'node:fs';
import { join } from 'node:path';
import { fromNodeHeaders } from 'better-auth/node';
import { audit, transaction } from './db.mjs';

export class Problem extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
export const requireValue = (condition, status, message) => { if (!condition) throw new Problem(status,message); };
export const digest = value => createHash('sha256').update(value).digest('hex');
export const identifier = value => { requireValue(typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value),400,'Invalid identifier'); return value; };
export const text = (value, max = 80) => { requireValue(typeof value === 'string' && value.trim().length >= 2 && value.length <= max,400,`Enter 2–${max} characters`); return value.trim(); };
const level = {viewer:0,member:1,admin:2,owner:2};

export function domain(config, db) {
  function entitlement(org) {
    const row = db.prepare('SELECT * FROM guard_subscription WHERE org_id=?').get(org);
    requireValue(row,404,'Organization not found');
    const allowed = row.status === 'trial' ? row.trial_ends > Date.now() : ['active','trialing'].includes(row.status) && row.period_end > Date.now();
    const plan = config.plans[row.plan];
    return {...row, allowed: Boolean(allowed && plan), limits: plan || config.plans.trial};
  }
  function membership(user, org, minimum = 0) {
    const member = db.prepare('SELECT * FROM member WHERE userId=? AND organizationId=?').get(user,org);
    requireValue(member && Object.hasOwn(level,member.role),403,'Organization access denied');
    requireValue(level[member.role] >= minimum,403,'Your role does not permit this operation');
    return member;
  }
  async function principal(req, auth) {
    const authorization = req.headers.authorization;
    if (authorization) {
      requireValue(/^Bearer arg_[A-Za-z0-9_-]{43}$/.test(authorization),401,'Invalid API key');
      const key = db.prepare('SELECT * FROM guard_key WHERE hash=? AND revoked_at IS NULL AND expires_at>?').get(digest(authorization.slice(7)),Date.now());
      requireValue(key,401,'API key expired or revoked');
      const member = membership(key.user_id,key.org_id);
      db.prepare('UPDATE guard_key SET last_used=? WHERE id=?').run(Date.now(),key.id);
      return {user:{id:key.user_id},key,member};
    }
    const session = await auth.api.getSession({headers:fromNodeHeaders(req.headers)});
    requireValue(session?.user,401,'Sign in to continue');
    requireValue(!config.production || session.user.emailVerified,403,'Verify your email first');
    return {user:session.user,session};
  }
  function organization(principal, org, minimum = 0) {
    identifier(org);
    requireValue(!principal.key,403,'This operation requires an account session');
    return membership(principal.user.id,org,minimum);
  }
  function project(principal, id, minimum = 0, scope = 'read') {
    identifier(id);
    const row = db.prepare('SELECT * FROM guard_project WHERE id=?').get(id);
    requireValue(row,404,'Project not found');
    const member = membership(principal.user.id,row.org_id,minimum);
    requireValue(level[member.role] >= 2 || db.prepare('SELECT 1 FROM guard_access WHERE project_id=? AND user_id=?').get(id,principal.user.id),403,'Project access denied');
    if (principal.key) requireValue(principal.key.project_id === id && principal.key.org_id === row.org_id && JSON.parse(principal.key.scopes).includes(scope),403,'API key scope does not allow this operation');
    return row;
  }
  function root(org, projectId) { return join(config.directory,'artifacts',identifier(org),identifier(projectId)); }
  function stored(org) {
    const base = join(config.directory,'artifacts',identifier(org));
    if (!existsSync(base)) return 0;
    let bytes = 0, count = 0;
    function walk(dir) {
      for (const name of readdirSync(dir)) {
        const path = join(dir,name), stat = lstatSync(path);
        requireValue(!stat.isSymbolicLink(),503,'Unsafe storage alias detected');
        requireValue(++count < 100000,429,'Artifact count limit reached');
        if (stat.isDirectory()) walk(path); else bytes += stat.size;
      }
    }
    walk(base); return bytes;
  }
  function usage(org) {
    const month = new Date().toISOString().slice(0,7), ent = entitlement(org);
    const counts = {imports:0,cases:0,runs:0,model_calls:0};
    for (const row of db.prepare('SELECT kind,count FROM guard_usage WHERE org_id=? AND month=?').all(org,month)) counts[row.kind] = row.count;
    return {month,...counts,storage_bytes:stored(org),projects:db.prepare('SELECT count(*) n FROM guard_project WHERE org_id=?').get(org).n,
      seats:db.prepare('SELECT count(*) n FROM member WHERE organizationId=?').get(org).n,limits:ent.limits,allowed:ent.allowed,plan:ent.plan,status:ent.status};
  }
  function createProject(principal, org, name) {
    organization(principal,org,1); name = text(name);
    return transaction(db,() => {
      const ent = entitlement(org);
      requireValue(ent.allowed,402,'Subscription inactive or trial ended');
      requireValue(db.prepare('SELECT count(*) n FROM guard_project WHERE org_id=?').get(org).n < ent.limits.projects,429,'Project limit reached');
      const id = randomUUID();
      db.prepare('INSERT INTO guard_project VALUES(?,?,?,?)').run(id,org,name,Date.now());
      db.prepare('INSERT INTO guard_access VALUES(?,?)').run(id,principal.user.id);
      audit(db,org,principal.user.id,'project.create',id);
      return {id,org_id:org,name};
    });
  }
  function createKey(principal, projectId, body, rotateId) {
    const row = project(principal,projectId,1);
    requireValue(!principal.key,403,'Create keys using an account session');
    const scopes = body.scopes || ['read','import','run'];
    requireValue(Array.isArray(scopes) && scopes.length > 0 && new Set(scopes).size === scopes.length && scopes.every(s => ['read','import','run'].includes(s)),400,'Invalid key scopes');
    const days = body.days || 90;
    requireValue(Number.isInteger(days) && days >= 1 && days <= 365,400,'Key lifetime must be 1–365 days');
    return transaction(db,() => {
      if (rotateId) {
        const old = db.prepare('SELECT * FROM guard_key WHERE id=? AND project_id=? AND revoked_at IS NULL').get(rotateId,projectId);
        requireValue(old && (old.user_id === principal.user.id || level[membership(principal.user.id,row.org_id).role] >= 2),403,'Key access denied');
        db.prepare('UPDATE guard_key SET revoked_at=? WHERE id=?').run(Date.now(),rotateId);
      }
      requireValue(db.prepare('SELECT count(*) n FROM guard_key WHERE org_id=? AND revoked_at IS NULL AND expires_at>?').get(row.org_id,Date.now()).n < 30,429,'API key limit reached');
      const id = randomUUID(), key = 'arg_' + randomBytes(32).toString('base64url'), name = text(body.name), created = Date.now();
      db.prepare('INSERT INTO guard_key(id,org_id,project_id,user_id,name,hash,prefix,scopes,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)').run(id,row.org_id,projectId,principal.user.id,name,digest(key),key.slice(0,12),JSON.stringify(scopes),created,created + days*86400000);
      audit(db,row.org_id,principal.user.id,rotateId ? 'key.rotate' : 'key.create',id);
      return {id,key,name,scopes,expires_at:created+days*86400000};
    });
  }
  return {entitlement,membership,principal,organization,project,root,stored,usage,createProject,createKey,level};
}
