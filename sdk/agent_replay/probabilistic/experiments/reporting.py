"""Escaped research reports and allowlisted public synthetic aggregates."""
import html
import json
from pathlib import Path
import re
from ...storage import save

SUMMARY_FIELDS=('success_rate','task_count','severe_failure_rate','empirical_loss_cvar','cumulative_reward',
    'cost_usd','tool_calls','duration_ms','planning_latency_ms','planning_tokens','prediction_calls','replans','labeled_count','label_coverage')
METRIC_FIELDS=('brier_score','log_loss','ece','sample_count','independent_groups','adaptive_ece','calibration_slope',
               'calibration_intercept','roc_auc','average_precision','positive_count','negative_count')
METHODS=('raw_agent','world_model_mc','platt','isotonic')
PROTOCOLS=('teacher_forced_one_step','free_running_open_loop','closed_loop_observed_execution')


def _number(v):
    import math
    return v if type(v) in (int,float) and math.isfinite(v) else None


def _numbers(record,fields):
    return {key:_number(record.get(key)) for key in fields}


def _valid_run(name):
    return name in ('forecasts','greedy','expected_value','mean_variance','cvar','uncertainty_cvar') or bool(re.fullmatch(
        r'h(?:1|2|3|4|5|8|10|16|32)-(?:open_loop|periodic|error_triggered|uncertainty_triggered)|screen-(?:k2|k8|m8|m32|h1|h5|h10|lambda0|lambda01|lambda1|alpha08|alpha095|underestimated_tail)',name))


def public_artifact(experiments):
    result={'schema_version':'reliability-public.v1','synthetic':True,'experiments':{}}
    for e in experiments:
        if e.get('schema_version')!='research-experiment.v1' or e.get('synthetic') is not True or e.get('status')!='completed' or e.get('experiment') not in ('calibration','risk_planning','horizon_reliability'):
            raise ValueError('Publish only completed explicitly synthetic research experiments')
        name=e['experiment']
        if name in result['experiments']:raise ValueError('Duplicate public experiment kind')
        out={'runs':{name:_numbers(r['summary'],SUMMARY_FIELDS) for name,r in e['runs'].items() if _valid_run(name)},
             'performance':_numbers(e['performance'],('elapsed_seconds','peak_rss_kib','artifact_bytes','task_executions','provider_cost_usd'))}
        out['candidate_examples']={}
        for run,r in e['runs'].items():
            example=r.get('candidate_example')
            if not _valid_run(run) or not example:continue
            selected=example.get('selected_candidate_id','')
            if not re.fullmatch(r'route-[0-7]|repair',selected):continue
            out['candidate_examples'][run]={'selected_candidate_id':selected,'candidates':[
                {'candidate_id':m['candidate_id'],**_numbers(m,('expected_reward','loss_cvar','return_variance','epistemic_uncertainty','score','sampled_rollouts','tail_sample_mass'))}
                for m in example.get('candidate_evaluations',[])[:8] if re.fullmatch(r'route-[0-7]|repair',m.get('candidate_id',''))]}
        calibration=e.get('calibration')
        if calibration:
            out['calibration']={}
            for split in ('validation','test','shift'):
                out['calibration'][split]={}
                for method in METHODS:
                    m=calibration['reports'].get(split,{}).get(method)
                    if not m:continue
                    out['calibration'][split][method]={**_numbers(m,METRIC_FIELDS),
                        'reliability_bins':[_numbers(b,('bin','count','predicted','observed')) for b in m.get('reliability_bins',[])[:50]],
                        'risk_coverage':[_numbers(r,('threshold','coverage','failure_risk')) for r in m.get('risk_coverage',[])[:1000]],
                        'confidence_intervals':{k:[_number(x) for x in v] if isinstance(v,list) and len(v)==2 else None for k,v in m.get('confidence_intervals',{}).items() if k in ('brier_score','log_loss','ece')}}
        out['horizons']={}
        for run,h in e.get('horizon_evaluations',{}).items():
            if not _valid_run(run):continue
            protocols={}
            for protocol in PROTOCOLS:
                if protocol not in h.get('by_protocol',{}):continue
                protocols[protocol]={}
                for horizon,metrics in h['by_protocol'][protocol].items():
                    if horizon not in ('1','2','3','4','5','8','10','16','32'):continue
                    protocols[protocol][horizon]={**_numbers(metrics,('sample_count','independent_groups','health_crps')),
                        'numeric_state':{f:_numbers(metrics.get('numeric_state',{}).get(f,{}),('mae','rmse')) for f in ('health','progress')},
                        'calibration':_numbers(metrics.get('calibration',{}),METRIC_FIELDS)}
            out['horizons'][run]={'by_protocol':protocols,'failure_count':len(h.get('failures',[])),
                'action_mismatched_windows':_number(h.get('simulated_only_or_action_mismatched_windows'))}
        out['comparisons']=[{'candidate':c['candidate'],'baseline':c['baseline'],'metric':c['metric'],
            **_numbers(c,('effect','p_value','holm_p_value','paired_samples','independent_groups')),
            'confidence_interval':[_number(v) for v in c['confidence_interval']] if c.get('confidence_interval') else None,
            'decision':c['decision'] if c.get('decision') in ('DIFFERENT','INCONCLUSIVE') else 'INCONCLUSIVE'}
            for c in e.get('comparisons',[]) if _valid_run(c.get('candidate','')) and _valid_run(c.get('baseline','')) and c.get('metric') in ('success','severe_failure','cumulative_reward','cost_usd','duration_ms')]
        result['experiments'][name]=out
    return result


def write_report(root,experiment):
    from ...serialization import canonical
    artifact=public_artifact([experiment]);save(Path(root)/'public-summary.json',artifact)
    esc=lambda v:html.escape(str(v),quote=True)
    rows=''.join('<tr><td>'+esc(name)+'</td>'+''.join('<td>'+esc(r['summary'].get(field))+'</td>' for field in SUMMARY_FIELDS)+'</tr>' for name,r in experiment['runs'].items())
    links=''.join('<li><a href="'+esc(name)+'/reports/index.html">'+esc(name)+' benchmark and real trajectories</a></li>' for name in experiment['runs'] if _valid_run(name))
    body='''<!doctype html><html lang="en"><meta charset="utf-8"><title>Reliability research report</title>
<style>body{font:16px system-ui;margin:32px}table{border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #ddd}pre{white-space:pre-wrap}.scroll{overflow:auto}</style>'''
    body+='<h1>'+esc(experiment['experiment'])+'</h1><p>Synthetic CPU research. Predictions are simulated; task outcomes come from real in-memory benchmark execution.</p><p>No production improvement is inferred.</p>'
    body+='<div class="scroll"><table><tr><th>Run</th>'+''.join('<th>'+esc(f)+'</th>' for f in SUMMARY_FIELDS)+'</tr>'+rows+'</table></div>'
    if experiment.get('calibration'):
        for split,methods in artifact['experiments'][experiment['experiment']].get('calibration',{}).items():
            body+='<h2>'+esc(split)+' calibration</h2>'
            for name,m in methods.items():
                dots=''.join(f'<circle cx="{40+240*b["predicted"]:.3f}" cy="{280-240*b["observed"]:.3f}" r="4"><title>n={b["count"]}</title></circle>' for b in m['reliability_bins'])
                body+='<h3>'+esc(name)+'</h3><svg role="img" aria-label="Reliability diagram" width="320" viewBox="0 0 320 320"><path d="M40 40V280H280M40 280L280 40" stroke="#aaa" fill="none"/>'+dots+'</svg><pre>'+esc(json.dumps({k:v for k,v in m.items() if k not in ('reliability_bins','risk_coverage')},indent=2))+'</pre>'
    body+='<h2>Replay and benchmark evidence</h2><ul>'+links+'</ul><h2>Paired effects and corrected comparisons</h2><pre>'+esc(json.dumps(experiment['comparisons'],indent=2))+'</pre><h2>Resource measurements</h2><pre>'+esc(json.dumps(experiment['performance'],indent=2))+'</pre><h2>Limitations</h2><ul>'+''.join('<li>'+esc(v)+'</li>' for v in experiment['limitations'])+'</ul></html>'
    (Path(root)/'index.html').write_text(body)
