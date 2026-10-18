import {test} from 'node:test';
import assert from 'node:assert/strict';
import {renderResearch,validateResearch} from '../dist/reliability.js';
import {validateArtifact} from '../dist/world-model.js';
const artifact=()=>({schema_version:'reliability-public.v1',synthetic:true,experiments:{
 calibration:{runs:{},calibration:{test:{raw_agent:{brier_score:.1,sample_count:20,independent_groups:10,reliability_bins:[{count:20,predicted:.8,observed:.7}],risk_coverage:[{coverage:1,failure_risk:.3}],confidence_intervals:{brier_score:[.08,.12]}}}}},
 risk_planning:{runs:{cvar:{success_rate:.9,severe_failure_rate:.01}},candidate_examples:{cvar:{selected_candidate_id:'route-1',candidates:[{candidate_id:'route-1',expected_reward:2,loss_cvar:-1,score:3}]}}},
 horizon_reliability:{runs:{'h4-open_loop':{success_rate:.5,task_count:20}},horizons:{}}}});
test('three labs render persisted aggregate results and candidate tail scores',()=>{
 const a=artifact();assert.equal(validateArtifact(a),a);
 assert.match(renderResearch(a,'calibration'),/Reliability diagram/);
 assert.match(renderResearch(a,'calibration'),/0.1000/);
 assert.match(renderResearch(a,'risk'),/CVaR loss/);
 assert.match(renderResearch(a,'risk'),/route-1/);
 assert.match(renderResearch(a,'horizon'),/Teacher-forced/);
 assert.match(renderResearch(a,'horizon'),/h4-open_loop/);
});
test('research viewer escapes imported labels and excludes malformed chart coordinates',()=>{
 const a=artifact();a.experiments.risk_planning.runs['<img src=x>']={success_rate:.5};
 const html=renderResearch(a,'risk');assert.match(html,/&lt;img/);assert.ok(!html.includes('<img'));
 a.experiments.calibration.calibration.test.raw_agent.reliability_bins.push({predicted:'" onload="x',observed:NaN});
 assert.ok(!renderResearch(a,'calibration').includes('onload='));
 assert.throws(()=>validateResearch({...a,synthetic:false}));
 assert.throws(()=>validateResearch({...a,experiments:{unknown:{}}}));
 assert.match(renderResearch({...a,experiments:{}},'risk'),/artifact required/);
});
