import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import {normalize,compareTraces,structuralDiff} from '../dist/v2.js';
const demo=JSON.parse(fs.readFileSync(new URL('../dist/regression-demo.json',import.meta.url)));
test('all demo traces validate as v2',()=>{demo.traces.forEach(normalize);});
test('actual candidate diff includes changed refund amount',()=>{const r=compareTraces(demo.traces.find(t=>t.id===demo.source_id),demo.traces.find(t=>t.id===demo.candidate_id));assert.ok(r.field_changes.flatMap(p=>p.changes).some(x=>x.path==='/input/value/amount_minor'));});
test('missing and null are distinct',()=>{const d=structuralDiff({a:null},{});assert.equal(d[0].before_present,true);assert.equal(d[0].after_present,false);});
test('reject cycles before import',()=>{const t=structuredClone(demo.traces[0]);t.steps[0].parent_id=t.steps[0].id;assert.throws(()=>normalize(t),/parent|cycle/);});
test('golden regression is fail-pass-fail',()=>assert.deepEqual(demo.results.map(r=>r.verdict),['fail','pass','fail']));
