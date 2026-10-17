import {esc,pretty} from './v2.js';
import {renderPlanning} from './planning.js';
let artifact=null,trace=null,tab='benchmark';
const numeric=v=>typeof v==='number'&&Number.isFinite(v)?v:null;
const number=v=>numeric(v)===null?'unknown':v.toFixed(4);
const panel=(name,body)=>`<section class="panel section-space"><div class="panel-head"><h2>${esc(name)}</h2></div><div class="panel-body">${body}</div></section>`;
export function validateArtifact(value){
  if(!value||typeof value!=='object')throw Error('JSON object required');
  if(value.schema_version==='2.0'&&Array.isArray(value.metadata?.planning?.sessions))return value;
  if(value.schema_version!=='world-model-public.v1'||!value.modes||!value.evaluations||Object.keys(value.modes).length>20)throw Error('Import a v2 planning trajectory or aggregate world-model-public.v1 artifact');
  return value;
}
export function renderWorld(value,view='benchmark'){
  validateArtifact(value);
  if(value.schema_version==='2.0'){
    if(['explorer','inspector'].includes(view))return renderPlanning(value);
    return panel('Evaluation artifact required','Import the aggregate experiment report to view benchmark and calibration results.');
  }
  if(['explorer','inspector'].includes(view))return panel('Planning trajectory required','Import a v2 trajectory to inspect hypotheses and real observations. Trajectory data stays in this browser tab.');
  if(view==='evaluation')return Object.entries(value.evaluations).map(([name,e])=>{
    const s=e.summary||{},bins=s.calibration?.reliability||[];
    const points=bins.slice(0,50).filter(b=>numeric(b.predicted)!==null&&numeric(b.observed)!==null&&b.predicted>=0&&b.predicted<=1&&b.observed>=0&&b.observed<=1).map(b=>`<circle cx="${40+b.predicted*240}" cy="${280-b.observed*240}" r="5"><title>n=${esc(b.count)}</title></circle>`).join('');
    return panel(name,`<p>${esc(e.model_id)} · ${esc(e.model_version)}</p><p>Exact match ${number(s.exact_match)} · Brier ${number(s.calibration?.brier_score)} · ECE ${number(s.calibration?.ece)} · Failure rate ${number(s.prediction_failure_rate)}</p><svg role="img" aria-label="Calibration curve" width="340" viewBox="0 0 340 330"><path d="M40 40V280H280M40 280L280 40" stroke="#aaa" fill="none"/>${points}<text x="70" y="315">Predicted success (0–1)</text></svg><p>Numeric confidence remains uncalibrated until held-out evidence supports it.</p><details><summary>Metric distributions and uncertainty</summary><pre>${esc(pretty(s))}</pre></details>`);
  }).join('');
  return panel('Paired planning benchmark',`<p>${esc(value.task_count)} matched synthetic tasks per mode. Costs are synthetic tool charges; latency includes capture and planning.</p><div class="table-scroll"><table><thead><tr><th>Mode</th><th>Passed / tasks</th><th>Success rate</th><th>Latency (ms)</th><th>Cost/task</th><th>Tool calls</th><th>Recovery rate</th></tr></thead><tbody>${Object.entries(value.modes).map(([name,s])=>`<tr><td>${esc(name)}</td><td>${esc(s.pass_count)} / ${esc(s.task_count)}</td><td>${number(s.success_rate)}</td><td>${number(s.distributions?.duration_ms?.mean)}</td><td>${number(s.distributions?.cost_usd?.mean)}</td><td>${number(s.distributions?.tool_call_count?.mean)}</td><td>${number(s.planning?.recovery_success_rate)}</td></tr>`).join('')}</tbody></table></div><p>${esc((value.limitations||[]).join(' · '))}</p><p>Quality gate: ${esc(value.quality_gate?.decision||'unavailable')} · ${esc((value.quality_gate?.reasons||[]).join(' · '))}</p><p>Online correction is replanning from real observations, not online model training.</p>`);
}
function render(){document.querySelector('#world-content').innerHTML=renderWorld(['explorer','inspector'].includes(tab)&&trace?trace:artifact||trace,tab);}
if(typeof document!=='undefined'){
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{tab=b.dataset.tab;render();}));
  document.querySelector('#world-import').addEventListener('change',async event=>{
    try{const file=event.target.files[0];if(!file||file.size>5*1024*1024)throw Error('Use a JSON artifact no larger than 5 MiB');
      const value=validateArtifact(JSON.parse(await file.text()));if(value.schema_version==='2.0'){trace=value;tab='explorer';}else artifact=value;
      document.querySelector('#world-status').textContent='Loaded locally. No data was uploaded.';render();
    }catch(error){document.querySelector('#world-status').textContent='Import rejected: '+error.message;}
  });
  fetch('/world-model-summary.json',{cache:'no-store'}).then(r=>r.json()).then(v=>{artifact=validateArtifact(v);render();}).catch(()=>{document.querySelector('#world-status').textContent='Import an evaluation artifact to begin.';});
}
