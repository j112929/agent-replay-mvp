import {test} from 'node:test';
import assert from 'node:assert/strict';
import {renderPlanning} from '../dist/planning.js';
import {readFile} from 'node:fs/promises';

test('planning comparison renders recorded candidates, predictions and actual observations safely',async()=>{
  const trace=JSON.parse(await readFile(new URL('../dist/planning-demo.json',import.meta.url),'utf8'));
  const html=renderPlanning(trace);
  assert.match(html,/Candidate/);assert.match(html,/Actual observation/);assert.match(html,/Predicted first observation/);
  trace.metadata.planning.sessions[0].selection.selection_reason='<img src=x onerror=alert(1)>';
  const escaped=renderPlanning(trace);assert.ok(!escaped.includes('<img'));assert.match(escaped,/&lt;img/);
  assert.equal(renderPlanning({metadata:{}}),'');
});
