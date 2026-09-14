import {validateTrace} from './core.js';
export const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const pretty=v=>JSON.stringify(v,null,2);
const envelope=(value,present=true)=>({present,...(present?{value}:{}),replayability:present?'complete':'unsupported'});
export function normalize(value){
  const t=structuredClone(value);
  if(t?.schema_version==='1.0'){
    validateTrace(t);const status=v=>v==='success'?'completed':v;
    return {...t,schema_version:'2.0',execution:{status:status(t.status),duration_ms:t.duration_ms},input:envelope(t.input,'input'in t),output:envelope(t.output,'output'in t),failures:t.failures||[],capture_health:{state:'partial',gaps:['Imported v1 history']},evaluation:{verdict:'not_evaluated'},steps:t.steps.map((s,i)=>({...s,parent_id:t.steps.some(p=>p.id===s.parent_id)?s.parent_id:null,seq:i+1,execution:{status:status(s.status),duration_ms:s.duration_ms,start_ms:s.start_ms},input:envelope(s.input,'input'in s),output:envelope(s.output,'output'in s)}))};
  }
  if(!t||t.schema_version!=='2.0'||!Array.isArray(t.steps)||t.steps.length>2000)throw Error('Use a v1 or v2 trajectory JSON.');
  for(const k of ['id','name','started_at'])if(typeof t[k]!=='string'||!t[k].trim()||t[k].length>240)throw Error('Invalid '+k);
  if(!Number.isFinite(Date.parse(t.started_at)))throw Error('Invalid timestamp');
  const checkExecution=e=>{if(!e||!['running','completed','error','interrupted'].includes(e.status)||typeof e.duration_ms!=='number'||!Number.isFinite(e.duration_ms)||e.duration_ms<0)throw Error('Invalid execution');};
  const checkValue=v=>{if(!v||typeof v.present!=='boolean'||!['complete','redacted','truncated','unsupported'].includes(v.replayability)||(v.present&&!('value'in v)))throw Error('Invalid value envelope');};
  checkExecution(t.execution);checkValue(t.input);checkValue(t.output);const ids=new Map();
  for(const s of t.steps){if(typeof s.id!=='string'||!s.id||ids.has(s.id)||!['agent','llm','tool'].includes(s.kind)||typeof s.name!=='string')throw Error('Invalid or duplicate step');checkExecution(s.execution);checkValue(s.input);checkValue(s.output);ids.set(s.id,s);}
  for(const s of t.steps){let p=s.parent_id,seen=new Set([s.id]);while(p){if(!ids.has(p)||seen.has(p))throw Error('Invalid parent or cycle');seen.add(p);p=ids.get(p).parent_id;}}
  return t;
}
export function structuralDiff(a,b,path='',rows=[]){
  if(JSON.stringify(a)===JSON.stringify(b))return rows;
  if(a&&b&&typeof a==='object'&&typeof b==='object'&&Array.isArray(a)===Array.isArray(b)){
    for(const k of [...new Set([...Object.keys(a),...Object.keys(b)])].sort())structuralDiff(a[k],b[k],path+'/'+k.replace(/~/g,'~0').replace(/\//g,'~1'),rows);
  }else rows.push({path,before_present:a!==undefined,after_present:b!==undefined,before:a??null,after:b??null});
  return rows;
}
export function compareTraces(source,candidate){
  const a=normalize(source),b=normalize(candidate),used=new Set(),aligned=[],added=[],ambiguous=[];
  const key=s=>JSON.stringify([s.boundary_id||s.name,s.kind,s.scope_path||[],s.lane_id??null,s.occurrence||1]);
  for(const right of b.steps){
    let matches=a.steps.filter(s=>!used.has(s.id)&&right.source_step_id===s.id),exact=matches.length>0;
    if(!matches.length)matches=a.steps.filter(s=>!used.has(s.id)&&key(s)===key(right));
    if(matches.length===1){const left=matches[0];used.add(left.id);const fields=s=>Object.fromEntries(['input','output','error','model','tool'].filter(k=>k in s).map(k=>[k,s[k]]).concat([['status',s.execution.status]]));aligned.push({source_id:left.id,candidate_id:right.id,confidence:exact||left.boundary_id?'exact':'inferred',changes:structuralDiff(fields(left),fields(right))});}
    else if(matches.length)ambiguous.push({candidate_id:right.id,source_ids:matches.map(s=>s.id)});else added.push(right.id);
  }
  const removed=a.steps.filter(s=>!used.has(s.id)).map(s=>s.id),changed=aligned.filter(p=>p.changes.length);
  return {schema_version:'1.0',source_trace_id:a.id,candidate_trace_id:b.id,aligned_pairs:aligned,field_changes:changed,added_steps:added,removed_steps:removed,ambiguous_groups:ambiguous,first_observed_divergence:changed[0]||(added.length||removed.length?{added_steps:added,removed_steps:removed}:null),output_changes:structuralDiff(a.output,b.output),coverage:{source_steps:a.steps.length,candidate_steps:b.steps.length,aligned:aligned.length}};
}
export function draftCase(trace,entrypoint,assertions){return {schema_version:'1.0',id:'case-'+trace.id,title:trace.name,entrypoint_id:entrypoint,source:{trace_path:'REPLACE_WITH_SOURCE_PATH',sha256:'COMPUTE_SOURCE_SHA256'},replay_spec:{scope:'agent',policy:'frozen',limits:{max_model_calls:0}},assertions,draft:true};}

export function normalizeResult(value){
  if(!value||typeof value.case_id!=='string'||!['pass','fail','error','inconclusive','not_evaluated'].includes(value.verdict)||!Array.isArray(value.assertions)||value.assertions.length>2000)throw Error('Invalid regression result');
  return structuredClone(value);
}
