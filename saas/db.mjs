import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, chmodSync } from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

export function database(directory) {
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const path = join(directory, 'accounts.sqlite');
  const db = new DatabaseSync(path);
  chmodSync(path, 0o600);
  db.exec('PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');
  return db;
}
export function migrate(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS guard_schema(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS guard_project(id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organization(id) ON DELETE CASCADE, name TEXT NOT NULL, created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS guard_access(project_id TEXT NOT NULL REFERENCES guard_project(id) ON DELETE CASCADE, user_id TEXT NOT NULL REFERENCES user(id) ON DELETE CASCADE, PRIMARY KEY(project_id,user_id));
    CREATE TABLE IF NOT EXISTS guard_key(id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organization(id) ON DELETE CASCADE, project_id TEXT NOT NULL REFERENCES guard_project(id) ON DELETE CASCADE, user_id TEXT NOT NULL REFERENCES user(id) ON DELETE CASCADE, name TEXT NOT NULL, hash TEXT UNIQUE NOT NULL, prefix TEXT NOT NULL, scopes TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, last_used INTEGER, revoked_at INTEGER);
    CREATE TABLE IF NOT EXISTS guard_subscription(org_id TEXT PRIMARY KEY REFERENCES organization(id) ON DELETE CASCADE, plan TEXT NOT NULL DEFAULT 'trial', status TEXT NOT NULL DEFAULT 'trial', trial_ends INTEGER NOT NULL, customer_id TEXT UNIQUE, subscription_id TEXT UNIQUE, period_end INTEGER, cancel_at_period_end INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS guard_usage(org_id TEXT NOT NULL REFERENCES organization(id) ON DELETE CASCADE, month TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0 CHECK(count>=0), PRIMARY KEY(org_id,month,kind));
    CREATE TABLE IF NOT EXISTS guard_job(id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organization(id) ON DELETE CASCADE, project_id TEXT NOT NULL REFERENCES guard_project(id) ON DELETE CASCADE, request_key TEXT NOT NULL, action TEXT NOT NULL, body_hash TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, UNIQUE(project_id,request_key));
    CREATE TABLE IF NOT EXISTS guard_suite(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES guard_project(id) ON DELETE CASCADE, name TEXT NOT NULL, cases TEXT NOT NULL, created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS guard_privacy(org_id TEXT PRIMARY KEY REFERENCES organization(id) ON DELETE CASCADE, fields TEXT NOT NULL DEFAULT '[]', updated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS guard_billing_event(id TEXT PRIMARY KEY, org_id TEXT, type TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, processed_at INTEGER);
    CREATE TABLE IF NOT EXISTS guard_audit(id TEXT PRIMARY KEY, org_id TEXT, actor_id TEXT, operation TEXT NOT NULL, resource TEXT, created_at INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS guard_job_project ON guard_job(project_id,created_at);
    CREATE INDEX IF NOT EXISTS guard_audit_org ON guard_audit(org_id,created_at);
    INSERT OR IGNORE INTO guard_schema VALUES(1, CAST(strftime('%s','now') AS INTEGER)*1000);
    UPDATE guard_job SET status='interrupted',result=NULL WHERE status='running';
  `);
}
export function transaction(db, fn) {
  db.exec('BEGIN IMMEDIATE');
  try { const result = fn(); db.exec('COMMIT'); return result; }
  catch (err) { db.exec('ROLLBACK'); throw err; }
}
export function audit(db, org, actor, operation, resource = null) {
  db.prepare('INSERT INTO guard_audit VALUES(?,?,?,?,?,?)').run(randomUUID(), org || null, actor || null, operation, resource, Date.now());
  // Bounded local audit retention; not a tamper-proof compliance archive.
  db.prepare('DELETE FROM guard_audit WHERE org_id IS ? AND id NOT IN (SELECT id FROM guard_audit WHERE org_id IS ? ORDER BY created_at DESC LIMIT 10000)').run(org || null, org || null);
}
