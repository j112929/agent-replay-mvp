"""CI validates observed evidence; no blanket assertion that planning always wins."""
import json
from pathlib import Path
import sys

root=Path(sys.argv[1]);value=json.loads((root/'experiment.json').read_text())
assert value['tasks_per_mode']==20 and value['real_external_operations']==0 and value['real_model_calls']==0
assert set(value['modes'])=={'baseline','planning','planning_with_replay_context'}
for mode, result in value['modes'].items():
    assert result['passed']+result['failed']==20 and result['failed']>0
    assert result['planning']['distributions']['real_tool_call_count']['count']==20
    assert (root/mode/'reports/index.html').exists()
assert value['paired_comparisons']['planning']['matched_count']==20
assert value['offline_prediction_comparison']['groups']==20
assert value['quality_gate']['decision']=='FAIL'
gate=json.loads((root/'ci-gate.json').read_text());assert gate['decision']=='FAIL' and gate['blocking']
assert any('cost' in reason or 'tool_call' in reason for reason in gate['reasons'])
print('Paired planning evidence and expected budget block verified; no production claim.')
