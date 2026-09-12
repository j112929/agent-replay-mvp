import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {validateTrace,forkStep,diffValues} from '../dist/core.js';
import {demoTraces} from '../dist/demo.js';
if(!globalThis.crypto)globalThis.crypto=webcrypto;

test('all bundled examples conform to the public trajectory schema',()=>{
  for(const trace of demoTraces)assert.deepEqual(validateTrace(trace),trace);
});
test('malformed imports fail before changing workspace state',()=>{
  for(const value of [null,{},[],{...demoTraces[0],schema_version:'9.0'},{...demoTraces[0],steps:[null]},{...demoTraces[0],duration_ms:-1},{...demoTraces[0],tags:{}},{...demoTraces[0],replay:'invalid'}])assert.throws(()=>validateTrace(value));
  const trace=structuredClone(demoTraces[0]);trace.steps[1].id=trace.steps[0].id;assert.throws(()=>validateTrace(trace),/unique/);
});
test('recorded failures stay failed and keep exact output without changing original',()=>{
  const trace=structuredClone(demoTraces[0]),before=structuredClone(trace);
  const result=forkStep(trace,'s6','recorded');
  assert.equal(result.status,'error');assert.deepEqual(result.steps[0].error,trace.steps[5].error);
  assert.equal(result.steps.length,1);assert.equal(result.replay.application_validated,false);assert.deepEqual(trace,before);validateTrace(result);
});
test('fixture fork removes error and excludes downstream results',()=>{
  const trace=demoTraces[0],fixture={refundable:true};
  const result=forkStep(trace,'s6','fixture',fixture);
  assert.equal(result.status,'success');assert.equal(result.duration_ms,0);assert.equal(result.steps[0].error,null);
  assert.deepEqual(result.steps[0].output,fixture);assert.equal(result.replay.scope,'step');assert.notEqual(result.id,trace.id);
  assert.equal(trace.steps[5].status,'error');assert.throws(()=>forkStep(trace,'s2','fixture',{}));
});
test('structural diff finds additions, removals, null, and nested array changes',()=>{
  const rows=diffValues({a:1,n:{v:[1,2]},gone:true},{a:null,n:{v:[1,3]},added:false});
  assert.deepEqual(rows.map(x=>x.path),['$.a','$.n.v.1','$.gone','$.added']);
  assert.deepEqual(diffValues({a:1},{a:1}),[]);
});
