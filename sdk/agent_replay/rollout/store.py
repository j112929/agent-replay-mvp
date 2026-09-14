"""Single-controller SQLite store. Every lease mutation is fenced and transactional."""
from contextlib import contextmanager
import json
import math
import re
from pathlib import Path
import sqlite3
import time
import uuid

from ..serialization import canonical, digest
from .metrics import MetricsMixin
from .artifacts import LocalObjects, S3Objects


class Conflict(ValueError):
    """A stale lease, policy, or idempotency key was presented."""


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_trajectory(value):
    if not isinstance(value, dict) or value.get('schema_version') != 'rollout.v1':
        raise ValueError('Expected rollout.v1 trajectory')
    if type(value.get('policy_version')) is not int or value['policy_version'] < 0 or type(value.get('seed')) is not int or not isinstance(value.get('policy_digest'), str) or re.fullmatch('[a-f0-9]{64}', value['policy_digest']) is None or not isinstance(value.get('environment'), str) or not value['environment']:
        raise ValueError('Invalid trajectory provenance')
    steps = value.get('transitions')
    if not isinstance(steps, list) or not steps or len(steps) > 2000:
        raise ValueError('Expected 1..2000 transitions')
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or not all(k in step for k in ('observation', 'action', 'next_observation', 'behavior_logprob', 'terminated', 'truncated')):
            raise ValueError('Incomplete transition')
        if not finite(step['behavior_logprob']) or step['behavior_logprob'] > 0:
            raise ValueError('Invalid behavior log probability')
        if type(step['terminated']) is not bool or type(step['truncated']) is not bool:
            raise ValueError('Termination flags must be booleans')
        if i < len(steps) - 1 and (step['terminated'] or step['truncated']):
            raise ValueError('Transition after episode end')
    if not (steps[-1]['terminated'] or steps[-1]['truncated']):
        raise ValueError('Episode must terminate or truncate')
    canonical(value)


class Store(MetricsMixin):
    def __init__(self, path, clock=time.time, objects=None):
        self.path = str(Path(path).resolve())
        self.clock = clock
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS measurements(id TEXT PRIMARY KEY,role TEXT NOT NULL,name TEXT NOT NULL,value REAL NOT NULL,recorded REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS measurements_time ON measurements(recorded);
                CREATE TABLE IF NOT EXISTS policies(version INTEGER PRIMARY KEY, digest TEXT NOT NULL, weights TEXT NOT NULL, parent INTEGER, batch TEXT UNIQUE, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', owner TEXT, token TEXT, deadline REAL, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3, result TEXT, receipt TEXT, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(kind,state,created);
                CREATE TABLE IF NOT EXISTS trajectories(id TEXT PRIMARY KEY, job TEXT UNIQUE NOT NULL, policy INTEGER NOT NULL, environment TEXT NOT NULL, seed INTEGER NOT NULL, digest TEXT NOT NULL, data TEXT NOT NULL, reward REAL, verifier TEXT, batch TEXT);
            ''')
            setting = db.execute("SELECT value FROM settings WHERE key='objects_root'").fetchone()
            configured = db.execute("SELECT value FROM settings WHERE key='objects_config'").fetchone()
            options = json.loads(configured['value']) if configured else None
            if objects is not None:
                self.objects = objects
            elif options and options['type'] == 's3':
                self.objects = S3Objects(options['bucket'],options['prefix'],options.get('endpoint_url'))
            else:
                self.objects = LocalObjects(options['root'] if options else setting['value'] if setting else self.path+'.objects')
            options = {'type':'local','root':str(self.objects.root)} if isinstance(self.objects,LocalObjects) else {'type':'s3','bucket':self.objects.bucket,'prefix':self.objects.prefix,'endpoint_url':self.objects.endpoint_url}
            db.execute("INSERT OR REPLACE INTO settings VALUES('objects_config',?)", (canonical(options),))
            if isinstance(self.objects, LocalObjects):
                db.execute("INSERT OR REPLACE INTO settings VALUES('objects_root',?)", (str(self.objects.root),))

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            if db.in_transaction:
                db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    def _policy(self, db, version=None):
        row = db.execute('SELECT * FROM policies ORDER BY version DESC LIMIT 1' if version is None else 'SELECT * FROM policies WHERE version=?', () if version is None else (version,)).fetchone()
        if row is None:
            raise ValueError('Policy not initialized')
        result = dict(row)
        result['weights'] = json.loads(result['weights'])
        return result

    def policy(self, version=None):
        with self.transaction() as db:
            return self._policy(db, version)

    def initialize(self, weights):
        encoded = canonical(weights)
        with self.transaction() as db:
            existing = db.execute('SELECT digest FROM policies WHERE version=0').fetchone()
            if existing and existing['digest'] != digest(weights):
                raise Conflict('Initial policy already exists with different weights')
            db.execute('INSERT OR IGNORE INTO policies VALUES(0,?,?,NULL,NULL,?)', (digest(weights), encoded, self.clock()))
            return self._policy(db)

    def _enqueue(self, db, kind, payload, key):
        encoded = canonical(payload)
        row = db.execute('SELECT kind,payload FROM jobs WHERE id=?', (key,)).fetchone()
        if row and (row['kind'] != kind or row['payload'] != encoded):
            raise Conflict('Idempotency key reused with different payload')
        db.execute('INSERT OR IGNORE INTO jobs(id,kind,payload,created) VALUES(?,?,?,?)', (key, kind, encoded, self.clock()))
        return key

    def enqueue(self, count, environment, seed, key, task=None):
        if type(count) is not int or not 1 <= count <= 10000 or type(seed) is not int or not isinstance(environment, str) or not environment or not isinstance(key, str) or not key:
            raise ValueError('Invalid rollout request')
        with self.transaction() as db:
            # Pin the first request policy even if an enqueue request is retried after training.
            old = db.execute('SELECT payload FROM jobs WHERE id=?', (f'rollout:{key}:0',)).fetchone()
            policy = self._policy(db, json.loads(old['payload'])['policy_version'] if old else None)
            ids = []
            for i in range(count):
                payload = {'policy_version': policy['version'], 'policy_digest': policy['digest'], 'environment': environment, 'seed': seed+i, 'group': key, 'group_size': count}
                if task is not None:
                    payload['task'] = task
                ids.append(self._enqueue(db, 'actor', payload, f'rollout:{key}:{i}'))
            return ids

    def claim(self, kind, owner, seconds=60):
        if kind not in ('actor', 'verifier', 'learner') or not isinstance(owner, str) or not owner or not finite(seconds) or not 1 <= seconds <= 3600:
            raise ValueError('Invalid lease request')
        with self.transaction() as db:
            now = self.clock()
            db.execute("UPDATE jobs SET state=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END, token=NULL, owner=NULL, deadline=NULL WHERE state='leased' AND deadline<=?", (now,))
            row = db.execute("SELECT * FROM jobs WHERE kind=? AND state='queued' ORDER BY created,id LIMIT 1", (kind,)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE jobs SET state='leased',owner=?,token=?,deadline=?,attempts=attempts+1 WHERE id=?", (owner, token, now+seconds, row['id']))
            result = dict(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())
            result['payload'] = json.loads(result['payload'])
            return result

    def _lease(self, db, job, token, kind, receipt=None):
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
        if row is None or row['kind'] != kind:
            raise ValueError('Unknown job')
        if row['state'] == 'done' and row['token'] == token and receipt == row['receipt']:
            return row, True
        if row['state'] != 'leased' or row['token'] != token or row['deadline'] <= self.clock():
            raise Conflict('Lease expired, superseded, or completed')
        return row, False

    def heartbeat(self, job, token, seconds=60):
        if not finite(seconds) or not 1 <= seconds <= 3600:
            raise ValueError('Invalid lease duration')
        with self.transaction() as db:
            row = db.execute('SELECT kind FROM jobs WHERE id=?', (job,)).fetchone()
            if row is None:
                raise ValueError('Unknown job')
            self._lease(db, job, token, row['kind'])
            deadline = self.clock()+seconds
            db.execute('UPDATE jobs SET deadline=? WHERE id=?', (deadline, job))
            return {'deadline': deadline}

    def fail(self, job, token, kind, error, retryable=True):
        if not isinstance(error, str) or type(retryable) is not bool:
            raise ValueError('Invalid failure report')
        with self.transaction() as db:
            row, _ = self._lease(db, job, token, kind)
            state = 'queued' if retryable and row['attempts'] < row['max_attempts'] else 'failed'
            db.execute('UPDATE jobs SET state=?,result=?,token=NULL,owner=NULL,deadline=NULL WHERE id=?', (state, canonical({'error': error[:1000]}), job))
            return {'state': state}

    def _done(self, db, job, receipt, result):
        row = db.execute('SELECT kind,created FROM jobs WHERE id=?', (job,)).fetchone()
        self._metric(db, row['kind'], 'job_seconds', max(0, self.clock()-row['created']))
        db.execute("UPDATE jobs SET state='done',receipt=?,result=? WHERE id=?", (receipt, canonical(result), job))
        return result

    def complete(self, job, token, trajectory):
        validate_trajectory(trajectory)
        receipt = digest(trajectory)
        with self.transaction() as db:
            row, repeated = self._lease(db, job, token, 'actor', receipt)
            if repeated:
                return json.loads(row['result'])
            payload = json.loads(row['payload'])
            for field in ('policy_version', 'policy_digest', 'environment', 'seed'):
                if trajectory.get(field) != payload[field]:
                    raise Conflict('Trajectory provenance mismatch: '+field)
            if 'task' in payload and trajectory.get('task') != payload['task']:
                raise Conflict('Trajectory task mismatch')
            trace_id = digest({'job': job, 'trajectory': trajectory})
            object_key = self.objects.put(trajectory)
            db.execute('INSERT INTO trajectories(id,job,policy,environment,seed,digest,data) VALUES(?,?,?,?,?,?,?)', (trace_id, job, payload['policy_version'], payload['environment'], payload['seed'], receipt, canonical({'$artifact': object_key})))
            self._metric(db, 'actor', 'rollouts', 1)
            tokens = trajectory.get('generation_tokens')
            if tokens is not None:
                if type(tokens) is not int or tokens < 0:
                    raise ValueError('Invalid generated token count')
                self._metric(db, 'actor', 'generated_tokens', tokens)
                self._metric(db, 'actor', 'tokenized_rollouts', 1)
            self._enqueue(db, 'verifier', {'trajectory_id': trace_id}, 'verify:'+trace_id)
            return self._done(db, job, receipt, {'trajectory_id': trace_id})

    def trajectory(self, trajectory_id):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM trajectories WHERE id=?', (trajectory_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown trajectory')
            result = dict(row)
            result['data'] = json.loads(result['data'])
            if '$artifact' in result['data']:
                result['data'] = self.objects.get(result['data']['$artifact'])
            if digest(result['data']) != result['digest']:
                raise ValueError('Trajectory checksum mismatch')
            return result

    def verify(self, job, token, reward, verifier):
        if not finite(reward) or not isinstance(verifier, str) or not verifier:
            raise ValueError('Finite reward and versioned verifier required')
        receipt = digest({'reward': reward, 'verifier': verifier})
        with self.transaction() as db:
            row, repeated = self._lease(db, job, token, 'verifier', receipt)
            if repeated:
                return json.loads(row['result'])
            trace_id = json.loads(row['payload'])['trajectory_id']
            db.execute('UPDATE trajectories SET reward=?,verifier=? WHERE id=?', (reward, verifier, trace_id))
            return self._done(db, job, receipt, {'trajectory_id': trace_id, 'reward': reward, 'verifier': verifier})

    def batch(self, size, environment, verifier, key, max_policy_lag=0):
        if type(size) is not int or not 1 <= size <= 10000 or not all(isinstance(x, str) and x for x in (environment, verifier, key)):
            raise ValueError('Invalid batch request')
        if type(max_policy_lag) is not int or not 0 <= max_policy_lag <= 16:
            raise ValueError('Invalid policy lag')
        with self.transaction() as db:
            job_id = 'learn:'+key
            old = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            if old:
                payload = json.loads(old['payload'])
                if (payload['size'], payload['environment'], payload['verifier'], payload.get('max_policy_lag', 0)) != (size, environment, verifier, max_policy_lag):
                    raise Conflict('Batch key reused')
                return job_id
            policy = self._policy(db)
            eligible = db.execute('SELECT policy,count(*) AS n FROM trajectories WHERE policy BETWEEN ? AND ? AND environment=? AND verifier=? AND reward IS NOT NULL AND batch IS NULL GROUP BY policy HAVING count(*)>=? ORDER BY policy DESC LIMIT 1', (max(0, policy['version']-max_policy_lag), policy['version'], environment, verifier, size)).fetchone()
            behavior_version = eligible['policy'] if eligible else policy['version']
            rows = db.execute('SELECT id FROM trajectories WHERE policy=? AND environment=? AND verifier=? AND reward IS NOT NULL AND batch IS NULL ORDER BY id LIMIT ?', (behavior_version, environment, verifier, size)).fetchall()
            if len(rows) < size:
                raise Conflict('Not enough verified, unconsumed samples for current policy')
            ids = [r['id'] for r in rows]
            self._enqueue(db, 'learner', {'policy_version': policy['version'], 'policy_digest': policy['digest'], 'behavior_policy_version': behavior_version, 'max_policy_lag': max_policy_lag, 'trajectory_ids': ids, 'size': size, 'environment': environment, 'verifier': verifier}, job_id)
            db.executemany('UPDATE trajectories SET batch=? WHERE id=?', [(job_id, i) for i in ids])
            return job_id

    def publish(self, job, token, weights, metrics):
        if not isinstance(metrics, dict) or not all(finite(v) for v in metrics.values()):
            raise ValueError('Metrics must contain finite numbers')
        receipt = digest({'weights': weights, 'metrics': metrics})
        with self.transaction() as db:
            row, repeated = self._lease(db, job, token, 'learner', receipt)
            if repeated:
                return json.loads(row['result'])
            payload = json.loads(row['payload'])
            current = self._policy(db)
            if current['version'] != payload['policy_version'] or current['digest'] != payload['policy_digest']:
                raise Conflict('Stale learner: active policy has changed')
            version = current['version']+1
            db.execute('INSERT INTO policies VALUES(?,?,?,?,?,?)', (version, digest(weights), canonical(weights), current['version'], job, self.clock()))
            sample_ids = payload['trajectory_ids']
            placeholders = ','.join('?' for _ in sample_ids)
            earliest = db.execute(f'SELECT min(j.created) FROM jobs j JOIN trajectories t ON j.id=t.job WHERE t.id IN ({placeholders})', sample_ids).fetchone()[0]
            self._metric(db, 'learner', 'consumed_rollouts', len(sample_ids))
            self._metric(db, 'learner', 'training_step_seconds', max(0, self.clock()-earliest))
            return self._done(db, job, receipt, {'policy_version': version, 'policy_digest': digest(weights), 'metrics': metrics})

    def scheduler_status(self, max_policy_lag=0):
        if type(max_policy_lag) is not int or not 0 <= max_policy_lag <= 16:
            raise ValueError('Invalid policy lag')
        with self.transaction() as db:
            current = self._policy(db)['version']
            return {'current': current, 'controller_time': self.clock(),
                'active': db.execute("SELECT count(*) FROM jobs WHERE kind='actor' AND state IN ('queued','leased')").fetchone()[0],
                'available': db.execute('SELECT count(*) FROM trajectories WHERE policy BETWEEN ? AND ? AND batch IS NULL', (max(0,current-max_policy_lag),current)).fetchone()[0],
                'learning': db.execute("SELECT count(*) FROM jobs WHERE kind='learner' AND state IN ('queued','leased')").fetchone()[0],
                'exhausted': db.execute("SELECT count(*) FROM jobs WHERE state='failed'").fetchone()[0]}

    def snapshot(self):
        with self.transaction() as db:
            policies = [dict(r) for r in db.execute('SELECT version,digest,parent,batch,created FROM policies ORDER BY version')]
            jobs = [dict(r) for r in db.execute('SELECT id,kind,state,owner,deadline,attempts,result FROM jobs ORDER BY created DESC LIMIT 200')]
            for job in jobs:
                job['result'] = json.loads(job['result']) if job['result'] else None
            return {'schema_version': 'rollout-snapshot.v1', 'policies': policies, 'jobs': jobs, 'counts': [dict(r) for r in db.execute('SELECT kind,state,count(*) AS count FROM jobs GROUP BY kind,state')], 'trajectories': [dict(r) for r in db.execute('SELECT id,policy,environment,seed,reward,verifier,batch FROM trajectories ORDER BY rowid DESC LIMIT 200')], 'total_trajectories': db.execute('SELECT count(*) FROM trajectories').fetchone()[0]}
