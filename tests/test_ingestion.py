import tempfile,unittest
from pathlib import Path
from agent_replay.ingestion import import_openinference
from agent_replay.schema import validate_trace

class IngestionTest(unittest.TestCase):
    def test_openinference_spans_preserve_raw_and_normalize(self):
        spans=[
          {"traceId":"abc","spanId":"root","name":"agent.run","startTimeUnixNano":1000000000,"endTimeUnixNano":4000000000,"attributes":{"openinference.span.kind":"AGENT"}},
          {"traceId":"abc","spanId":"llm","parentSpanId":"root","name":"chat","startTimeUnixNano":1500000000,"endTimeUnixNano":2000000000,"attributes":{"openinference.span.kind":"LLM","input.value":"hello","output.value":"plan"}},
          {"traceId":"abc","spanId":"tool","parentSpanId":"root","name":"search","startTimeUnixNano":2100000000,"endTimeUnixNano":3000000000,"attributes":{"openinference.span.kind":"TOOL","input.value":"q","output.value":"result"}},
        ]
        with tempfile.TemporaryDirectory() as d:
            result=import_openinference(spans,d);t=result["trace"];validate_trace(t)
            self.assertEqual(t["id"],"abc");self.assertEqual([s["kind"] for s in t["steps"]],["llm","tool"])
            self.assertTrue(Path(result["raw_path"]).exists());self.assertTrue(Path(result["trace_path"]).exists())
            self.assertEqual(t["provenance"]["ingestion"]["source"],"openinference")
            self.assertIn("external_attributes",t["steps"][0])

if __name__=="__main__":unittest.main()
