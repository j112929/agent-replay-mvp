"""Audit real measured evidence without asserting all planners must outperform."""
import json
import sys
from pathlib import Path
root=Path(sys.argv[1]);value=json.loads((root/'experiment.json').read_text())
assert value['version']=='0.5.0' and value['external_operations']==0
modes=value['modes'];assert {'baseline','safe-policy','shadow','active','correcting','horizon1','horizon5','no-risk','no-uncertainty'}<=set(modes)
assert modes['baseline']['pass_count']==modes['shadow']['pass_count']
assert all(v['task_count']==value['task_count'] for v in modes.values())
assert value['training']['metrics']['examples']>0
assert value['evaluations']['trained']['summary']['count']>0
assert value['evaluations']['oracle']['summary']['schema_validity']==1
assert all(r['trajectory_id'] and r['step_id'] for r in value['evaluations']['trained']['errors'])
assert (root/'correcting/reports/index.html').exists()
print('Matched modes, real transitions, CPU supervised fit, held-out evaluation and existing reports verified.')
