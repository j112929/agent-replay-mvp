export const VERSION = '1.0';
export const clone = value => structuredClone(value);
const validText = (v, key, max = 240) => { if (typeof v !== 'string' || !v.trim() || v.length > max) throw new Error(`${key} must be a non-empty string (max ${max} characters).`); };
export function validateTrace(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected a trajectory JSON object.');
  if (value.schema_version !== VERSION) throw new Error('Unsupported schema_version. Use the SDK v1.0 JSON format.');
  validText(value.id, 'id'); validText(value.name, 'name');
  if (!['success', 'error', 'running'].includes(value.status)) throw new Error('Invalid trajectory status.');
  if (typeof value.started_at !== 'string' || !Number.isFinite(Date.parse(value.started_at))) throw new Error('started_at must be an ISO date.');
  if (value.tags !== undefined && (!Array.isArray(value.tags) || !value.tags.every(t => typeof t === 'string'))) throw new Error('tags must be an array of strings.');
  if (value.metadata !== undefined && (!value.metadata || typeof value.metadata !== 'object' || Array.isArray(value.metadata))) throw new Error('metadata must be an object.');
  if (value.replay !== undefined) {
    const r = value.replay;
    if (!r || typeof r !== 'object' || !['recorded','fixture','live','tool','agent_rerun'].includes(r.mode) || !['step','agent'].includes(r.scope) || typeof r.application_validated !== 'boolean') throw new Error('Invalid replay metadata.');
    validText(r.source_trace_id, 'replay.source_trace_id');
    if (r.scope === 'step') validText(r.source_step_id, 'replay.source_step_id');
  }
  if (!Number.isFinite(value.duration_ms) || value.duration_ms < 0) throw new Error('duration_ms must be a positive number or zero.');
  if (!Array.isArray(value.steps) || value.steps.length > 2000) throw new Error('steps must be an array with at most 2,000 items.');
  const ids = new Set();
  for (const step of value.steps) {
    if (!step || typeof step !== 'object') throw new Error('Each step must be an object.');
    validText(step.id, 'step.id'); validText(step.name, 'step.name');
    if (ids.has(step.id)) throw new Error('Step IDs must be unique.'); ids.add(step.id);
    if (!['llm', 'tool', 'agent'].includes(step.kind)) throw new Error(`Unknown step kind: ${step.kind}`);
    if (!['success', 'error', 'running'].includes(step.status)) throw new Error('Invalid step status.');
    if (step.usage !== undefined && (!step.usage || typeof step.usage !== 'object' || Array.isArray(step.usage))) throw new Error('usage must be an object.');
    if (step.usage?.total_tokens !== undefined && (!Number.isSafeInteger(step.usage.total_tokens) || step.usage.total_tokens < 0)) throw new Error('total_tokens must be a nonnegative integer.');
    for (const key of ['duration_ms', 'start_ms']) if (!Number.isFinite(step[key]) || step[key] < 0) throw new Error(`${key} must be a nonnegative number.`);
  }
  return clone(value);
}
export function forkStep(trace, stepId, mode, patch) {
  const source = trace.steps.find(s => s.id === stepId);
  if (!source) throw new Error('Step not found.');
  if (!['recorded', 'fixture'].includes(mode)) throw new Error('Invalid replay mode.');
  if (mode === 'fixture' && source.kind !== 'tool') throw new Error('Fixture replay requires a tool step.');
  const step = clone(source);
  step.id = crypto.randomUUID(); step.source_step_id = source.id; step.start_ms = 0; step.replay_mode = mode;
  if (mode === 'fixture') { step.output = clone(patch); step.error = null; step.status = 'success'; step.duration_ms = 0; delete step.usage; }
  return { schema_version: VERSION, id: crypto.randomUUID(), name: trace.name, started_at: new Date().toISOString(), status: step.status,
    duration_ms: mode === 'recorded' ? step.duration_ms : 0, steps: [step], tags: ['replay'],
    replay: { source_trace_id: trace.id, source_step_id: source.id, mode, scope: 'step', outcome: mode === 'fixture' ? 'fixture_applied' : 'recorded_observation', application_validated: false },
    metadata: { note: 'Single-step replay. Downstream steps were not executed. Recorded timing is preserved only in recorded mode.' } };
}
export function diffValues(before, after, path = '$', rows = []) {
  if (JSON.stringify(before) === JSON.stringify(after)) return rows;
  if (before && after && typeof before === 'object' && typeof after === 'object' && Array.isArray(before) === Array.isArray(after)) {
    for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) diffValues(before[key], after[key], `${path}.${key}`, rows);
  } else rows.push({path, before, after});
  return rows;
}
export const formatDuration = n => n < 1000 ? `${Math.round(n)} ms` : `${(n / 1000).toFixed(2)} s`;
export const pretty = v => v === undefined ? '—' : JSON.stringify(v, null, 2);
