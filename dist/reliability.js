import {esc} from './v2.js';
const n=v=>typeof v==='number'&&Number.isFinite(v)?v:null;
const f=v=>n(v)===null?'unknown':v.toFixed(4);
const panel=(title,body)=>`<section class="panel section-space"><div class="panel-head"><h2>${esc(title)}</h2></div><div class="panel-body">${body}</div></section>`;
const colors=['#4263eb','#e8590c','#2b8a3e','#ae3ec9','#c92a2a'];
const table=(headers,rows)=>`<div class="table-scroll"><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(c=>`<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
export function validateResearch(value){
  if(value?.schema_version!=='reliability-public.v1'||value.synthetic!==true||!value.experiments||typeof value.experiments!=='object'||Array.isArray(value.experiments)||Object.keys(value.experiments).length>3)throw Error('Use a published synthetic reliability-public.v1 artifact');
  for(const [name,e] of Object.entries(value.experiments)){
    if(!['calibration','risk_planning','horizon_reliability'].includes(name)||!e||typeof e!=='object'||Object.keys(e.runs||{}).length>64||Object.keys(e.horizons||{}).length>64)throw Error('Invalid research collections');
    for(const methods of Object.values(e.calibration||{}))for(const m of Object.values(methods||{})){
      if(!m||typeof m!=='object'||!Array.isArray(m.reliability_bins)||m.reliability_bins.length>50||!Array.isArray(m.risk_coverage)||m.risk_coverage.length>1000)throw Error('Invalid calibration series');
    }
    for(const example of Object.values(e.candidate_examples||{}))if(!example||!Array.isArray(example.candidates)||example.candidates.length>8)throw Error('Invalid candidate series');
  }
  return value;
}
function chart(title,series,{maxX=1,maxY=1,diagonal=false}={}){
  let body='<path d="M45 25V265H365" stroke="#aab" fill="none"/>';
  if(diagonal)body+='<path d="M45 265L365 25" stroke="#aab" stroke-dasharray="4" fill="none"/>';
  const safeMaxX=maxX>0&&Number.isFinite(maxX)?maxX:1,safeMaxY=maxY>0&&Number.isFinite(maxY)?maxY:1;
  series.slice(0,5).forEach((s,i)=>{
    const points=s.points.slice(0,1000).filter(p=>Array.isArray(p)&&n(p[0])!==null&&n(p[1])!==null&&p[0]>=0&&p[1]>=0&&p[0]<=safeMaxX&&p[1]<=safeMaxY);
    body+=`<polyline points="${points.map(([x,y])=>`${45+320*x/safeMaxX},${265-240*y/safeMaxY}`).join(' ')}" stroke="${colors[i]}" fill="none"/>`;
    body+=points.map(([x,y])=>`<circle cx="${45+320*x/safeMaxX}" cy="${265-240*y/safeMaxY}" r="3" fill="${colors[i]}"><title>${esc(s.name)}: ${f(x)}, ${f(y)}</title></circle>`).join('');
    body+=`<text x="45" y="${305+i*18}" fill="${colors[i]}">${esc(s.name)}</text>`;
  });
  return `<figure style="margin-inline:0"><svg style="max-width:100%;height:auto" role="img" aria-label="${esc(title)}" width="440" viewBox="0 0 440 410">${body}<text x="45" y="285">0 → ${f(safeMaxX)} · y: 0 → ${f(safeMaxY)}</text></svg><figcaption>${esc(title)}</figcaption></figure>`;
}
export function renderResearch(value,view='calibration',options={}){
  validateResearch(value);
  const kind={calibration:'calibration',risk:'risk_planning',horizon:'horizon_reliability'}[view]||'calibration',e=value.experiments[kind];
  if(!e)return panel('Experiment artifact required','Run and publish this experiment, then import its public-summary.json. The static workspace does not execute Python.');
  const notice='<p>SYNTHETIC CPU RESEARCH · persisted experiment results · sample counts and uncertainty apply. These results do not establish production reliability.</p>';
  if(view==='calibration'){
    const split=['validation','test','shift'].includes(options.split)?options.split:'test';
    const data=e.calibration?.[split]||{},methods=Object.entries(data).slice(0,4);
    const controls=`<label>Evaluation split <select id="research-split">${['validation','test','shift'].map(s=>`<option ${s===split?'selected':''}>${s}</option>`).join('')}</select></label>`;
    const reliability=methods.map(([name,m])=>({name,points:(m.reliability_bins||[]).slice(0,50).map(b=>[b.predicted,b.observed])}));
    const risk=methods.map(([name,m])=>({name,points:(m.risk_coverage||[]).slice(0,1000).map(r=>[r.coverage,r.failure_risk])}));
    return panel('Calibration Lab',notice+controls+table(['Method','Brier','Log loss','ECE','Adaptive ECE','AUROC','AP','Samples / groups','Brier 95% CI'],methods.map(([name,m])=>[name,f(m.brier_score),f(m.log_loss),f(m.ece),f(m.adaptive_ece),f(m.roc_auc),f(m.average_precision),`${m.sample_count} / ${m.independent_groups}`,(m.confidence_intervals?.brier_score||[]).map(f).join(' – ')||'unknown']))+chart('Reliability diagram: predicted vs verified success',reliability,{diagonal:true})+chart('Selective failure risk vs coverage',risk)+'<p>ECE depends on bins. Bootstrap resamples scenario groups. Platt and isotonic fit training families only; shift results remain separate. Inspect version drift by importing a second run artifact.</p>');
  }
  if(view==='risk'){
    const rows=Object.entries(e.runs||{}).slice(0,64),chosen=Object.hasOwn(e.candidate_examples||{},options.run)?options.run:'cvar';
    const example=e.candidate_examples?.[chosen];
    const candidates=example?table(['Candidate','Selected','Expected reward','CVaR loss','Return variance','Epistemic heuristic','Score','Samples / tail mass'],(example.candidates||[]).slice(0,8).map(c=>[c.candidate_id,c.candidate_id===example.selected_candidate_id?'yes':'',f(c.expected_reward),f(c.loss_cvar),f(c.return_variance),f(c.epistemic_uncertainty),f(c.score),`${c.sampled_rollouts} / ${f(c.tail_sample_mass)}`])):'No candidate example in this artifact.';
    const controls=`<label>Decision example <select id="research-run">${Object.keys(e.candidate_examples||{}).slice(0,64).map(name=>`<option value="${esc(name)}" ${name===chosen?'selected':''}>${esc(name)}</option>`).join('')}</select></label>`;
    return panel('Risk Planning Lab',notice+table(['Planner','Success','Severe failures','Empirical tail loss','Reward','Planning ms','Tools','Synthetic cost'],rows.map(([name,s])=>[name,f(s.success_rate),f(s.severe_failure_rate),f(s.empirical_loss_cvar),f(s.cumulative_reward),f(s.planning_latency_ms),f(s.tool_calls),f(s.cost_usd)]))+controls+candidates+'<p>All planners receive the same candidate sets and per-decision sampling budgets. CVaR averages the worst (1−α) loss mass; few tail samples can miss rare failures. Alternative futures are simulated-only.</p>');
  }
  const names=['open_loop','periodic','error_triggered','uncertainty_triggered'];
  const series=names.map(strategy=>({name:strategy,points:Object.entries(e.runs||{}).filter(([name])=>name.endsWith('-'+strategy)).map(([name,s])=>[Number(name.split('-')[0].slice(1)),s.success_rate]).sort((a,b)=>a[0]-b[0])}));
  const errors=names.map(strategy=>({name:strategy,points:Object.entries(e.horizons||{}).filter(([name])=>name.endsWith('-'+strategy)).map(([name,h])=>{
    const horizon=Number(name.split('-')[0].slice(1)),protocol=strategy==='open_loop'?'free_running_open_loop':'closed_loop_observed_execution';
    return [horizon,h.by_protocol?.[protocol]?.[horizon]?.numeric_state?.health?.rmse];
  }).sort((a,b)=>a[0]-b[0])}));
  const maxH=Math.max(1,...series.flatMap(s=>s.points.map(p=>p[0]))),maxError=Math.max(1,...errors.flatMap(s=>s.points.map(p=>n(p[1])??0)));
  return panel('Horizon Reliability Lab',notice+chart('Verified success vs horizon (matched tasks and trial seeds)',series,{maxX:maxH})+chart('Numeric health RMSE vs horizon; separate execution protocols',errors,{maxX:maxH,maxY:maxError})+table(['Run','Success','Replans/task','Tools','Planning ms','Samples','Counterfactual windows excluded'],Object.entries(e.runs||{}).slice(0,64).map(([name,s])=>[name,f(s.success_rate),f(s.replans),f(s.tool_calls),f(s.planning_latency_ms),s.task_count,e.horizons?.[name]?.action_mismatched_windows??'unknown']))+'<p>Teacher-forced one-step, free-running open-loop and closed-loop execution are stored separately. Closed-loop windows with changed actions are excluded from counterfactual labeling. Trigger thresholds are synthetic screening settings.</p>');
}
