"""Published schemas and runtime imports agree for all shipped examples."""
import json
from pathlib import Path
import unittest
try:
    import jsonschema
except ImportError:
    jsonschema=None
from agent_replay.schema import validate_trace

@unittest.skipIf(jsonschema is None,'Install .[dev] to validate published JSON schemas')
class ContractTests(unittest.TestCase):
    def test_v2_examples_match_published_schema(self):
        root=Path(__file__).resolve().parents[1];schema=json.loads((root/'schema/trajectory-v2.schema.json').read_text());demo=json.loads((root/'dist/regression-demo.json').read_text())
        for trace in demo['traces']:jsonschema.validate(trace,schema);validate_trace(trace)
    def test_all_schemas_are_well_formed(self):
        root=Path(__file__).resolve().parents[1]
        for p in (root/'schema').glob('*.json'):jsonschema.Draft202012Validator.check_schema(json.loads(p.read_text()))
    def test_results_match_schema(self):
        root=Path(__file__).resolve().parents[1];schema=json.loads((root/'schema/regression-result.schema.json').read_text());demo=json.loads((root/'dist/regression-demo.json').read_text())
        for result in demo['results']:jsonschema.validate(result,schema)
