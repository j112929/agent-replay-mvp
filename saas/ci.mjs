// Dependency-free CI client. Runs supplied evidence, never customer agent code.
import {readFile, writeFile} from 'node:fs/promises';
import {createHash, randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';

export async function ci(args, env = process.env) {
  const flag = name => args[args.indexOf('--'+name)+1];
  for (const name of ['server','project','case','baseline','candidate']) if (!args.includes('--'+name) || !flag(name) || flag(name).startsWith('--')) throw new Error('Missing --'+name);
  const origin = new URL(flag('server'));
  if (origin.protocol !== 'https:' && !(origin.protocol === 'http:' && ['127.0.0.1','localhost'].includes(origin.hostname))) throw new Error('Use HTTPS or a loopback test server');
  if (!env.GUARD_API_KEY || !/^arg_[A-Za-z0-9_-]{43}$/.test(env.GUARD_API_KEY)) throw new Error('Set GUARD_API_KEY as a CI secret');
  const project = flag('project');
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(project)) throw new Error('Invalid project ID');
  const request = async (resource,value,key) => {
    const response = await fetch(origin.origin+'/api/saas/projects/'+project+'/'+resource,{method:'POST',redirect:'error',signal:AbortSignal.timeout(25000),headers:{Authorization:'Bearer '+env.GUARD_API_KEY,'Content-Type':'application/json','Idempotency-Key':key},body:JSON.stringify(value)});
    if (!response.ok) throw new Error('Guard request failed (HTTP '+response.status+'); inspect your key, permissions and usage limits');
    return response.json();
  };
  const evidence = [];
  for (const kind of ['baseline','candidate']) {
    const raw = await readFile(flag(kind));
    if (raw.length > 1048576) throw new Error('Trace exceeds 1 MiB');
    let trace; try {trace = JSON.parse(raw);} catch {throw new Error('Malformed capture JSON');}
    const hash = createHash('sha256').update(raw).digest('hex');
    const imported = await request('import',{trace,format:trace.schema_version ? 'native' : 'generic'},'ci-import-'+hash);
    evidence.push(imported.id);
  }
  let proof;
  if (args.includes('--release-evidence')) {
    const raw = await readFile(flag('release-evidence'));
    if (raw.length > 1048576) throw new Error('Release evidence exceeds 1 MiB');
    proof = JSON.parse(raw);
  }
  const gate = await request(proof ? 'release' : 'evaluate',{case_id:flag('case'),baseline:evidence[0],candidate:evidence[1],...(proof ? {evidence:proof} : {})},'ci-run-'+randomUUID());
  const summary = Object.fromEntries(['id','case_id','case_version','decision','classification','blocking','versions','reasons','exit_code','external_operations','model_calls','release_eligible'].map(k=>[k,gate[k]]));
  if (proof && (gate.decision !== 'PASS' || gate.release_eligible !== true) && !gate.exit_code) summary.exit_code = 3;
  summary.report_url = origin.origin+'/debugger.html#runs';
  if (args.includes('--output')) await writeFile(flag('output'),JSON.stringify(summary,null,2)+'\n',{mode:0o600});
  return summary;
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {const summary=await ci(process.argv.slice(2)); console.log(JSON.stringify(summary)); process.exitCode=summary.exit_code;}
  catch {console.error(JSON.stringify({decision:'INCONCLUSIVE',blocking:true,error:'CI evaluation unavailable; check configuration without printing credentials or traces'}));process.exitCode=3;}
}
