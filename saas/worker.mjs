import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { StringDecoder } from 'node:string_decoder';
import { fileURLToPath } from 'node:url';
import { Problem } from './domain.mjs';

export function workers(config) {
  let active = 0;
  const tenants = new Set();
  const sdk = resolve(fileURLToPath(new URL('../sdk',import.meta.url)));
  async function call(project, root, action, body = {}, limits, sensitiveFields = []) {
    if (active >= config.maxJobs || tenants.has(project.org_id)) throw new Problem(429,'A job is already running; retry shortly');
    active++; tenants.add(project.org_id);
    try {
      mkdirSync(root,{recursive:true,mode:0o700});
      return await new Promise((resolveValue,reject) => {
        const child = spawn(config.python,['-m','agent_replay.saas_worker',root,project.org_id,project.id], {
          cwd:root, detached:true, stdio:['pipe','pipe','pipe'],
          env:{PATH:process.env.PATH || '/usr/bin:/bin',PYTHONPATH:sdk,PYTHONDONTWRITEBYTECODE:'1',LANG:'C.UTF-8'}
        });
        let output = '', bytes = 0, completed = false;
        const decoder = new StringDecoder('utf8');
        const stop = () => { try { process.kill(-child.pid,'SIGKILL'); } catch { child.kill('SIGKILL'); } };
        const finish = (error,value) => { if (completed) return; completed = true; clearTimeout(timer); error ? reject(error) : resolveValue(value); };
        const timer = setTimeout(() => {stop(); finish(new Problem(504,'Evaluation timed out'));},config.workerTimeout);
        child.on('error',() => finish(new Problem(503,'Python SDK unavailable; check server setup')));
        child.stdout.on('data',chunk => { bytes += chunk.length; if (bytes > 8*1024*1024) {stop();finish(new Problem(413,'Result exceeds output limit'));} else output += decoder.write(chunk); });
        child.stderr.resume(); // Never forward captured content or process errors into logs.
        child.stdin.on('error',() => {});
        child.on('close',() => {
          output += decoder.end();
          try { const result = JSON.parse(output); if (!result.ok) throw new Error(); finish(null,result.value); }
          catch { finish(new Problem(422,'Operation rejected: check schema, fixtures, approval and quota limits')); }
        });
        child.stdin.end(JSON.stringify({action,body,limits,sensitive_fields:sensitiveFields}));
      });
    } finally {active--; tenants.delete(project.org_id);}
  }
  return {call,get active(){return active;}};
}
