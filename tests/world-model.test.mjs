import {test} from 'node:test';
import assert from 'node:assert/strict';
import {renderWorld,validateArtifact} from '../dist/world-model.js';
test('public world model viewer is data-only and escapes imported names',()=>{
 const value={schema_version:'world-model-public.v1',task_count:20,modes:{'<img src=x>':{task_count:20,pass_count:4,success_rate:.2}},evaluations:{oracle:{model_id:'oracle',model_version:'v1',summary:{exact_match:1,calibration:{brier_score:0,reliability:[{predicted:1,observed:1,count:20}]}}}},limitations:[]};
 assert.ok(renderWorld(value).includes('&lt;img'));assert.ok(!renderWorld(value).includes('<img'));
 assert.ok(renderWorld(value,'evaluation').includes('Calibration curve'));
 assert.ok(renderWorld(value,'explorer').includes('trajectory required'));
 assert.throws(()=>validateArtifact({schema_version:'arbitrary-code'}));
});
