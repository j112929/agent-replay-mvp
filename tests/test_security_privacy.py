import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from agent_replay.capture import Capture, tool
from agent_replay.security.sanitizer import Sanitizer, REDACTED, FAILED
from agent_replay.storage import read, save
from agent_replay.bundle import export_bundle


class PrivacyTests(unittest.TestCase):
    def test_structured_nested_and_encoded_json(self):
        token = 'synthetic-only-credential'
        for policy in ('STRICT', 'STANDARD', 'CUSTOM'):
            s = Sanitizer(policy)
            cleaned = s.sanitize({'headers': {'Authorization': token}, 'a': [{'apiKey': token}],
                'arguments': json.dumps({'password': token})})
            self.assertNotIn(token, json.dumps(cleaned))
            self.assertGreater(s.metadata()['redacted_field_count'], 0)

    def test_patterns_custom_pii_and_false_positives(self):
        self.assertEqual(Sanitizer().sanitize({'tokens': 42, 'token_count': 10, 'author': 'Joe'}),
                         {'tokens': 42, 'token_count': 10, 'author': 'Joe'})
        self.assertEqual(Sanitizer(sensitive_fields=['customer_id']).sanitize({'customer_id': '123'})['customer_id'], REDACTED)
        self.assertNotIn('a@example.invalid', Sanitizer('STRICT').sanitize('contact a@example.invalid'))
        self.assertEqual(Sanitizer().sanitize('contact a@example.invalid'), 'contact a@example.invalid')
        for secret in ('sk-synthetic0123456789', 'Bearer synthetic0123456789', 'password=canary123',
                       '-----BEGIN PRIVATE KEY-----\nCANARY\n-----END PRIVATE KEY-----'):
            self.assertNotIn(secret, Sanitizer().sanitize(secret))

    def test_failure_is_closed_without_secret_error(self):
        class Broken:
            def model_dump(self, **kwargs): raise ValueError('secret_canary_DO_NOT_LOG')
        s = Sanitizer()
        self.assertEqual(s.sanitize(Broken()), FAILED)
        self.assertEqual(s.metadata()['status'], 'failed_closed')
        self.assertNotIn('secret_canary', json.dumps(s.metadata()))
        cycle = []; cycle.append(cycle)
        self.assertIn(FAILED, str(s.sanitize(cycle)))

    def test_capture_journal_errors_outputs_and_export(self):
        secret = 'synthetic0123456789'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            @tool
            def sensitive(): return {'password': secret, 'nested': {'api_key': secret}}
            with self.assertRaises(RuntimeError):
                with Capture('private', root/'traces', input={'access_token': secret}, durability='sync') as capture:
                    self.assertEqual(sensitive()['password'], secret)
                    raise RuntimeError('password=' + secret)
            raw = '\n'.join(p.read_text() for p in root.rglob('*') if p.is_file())
            self.assertNotIn(secret, raw)
            trace = read(capture.path)
            self.assertEqual(trace['steps'][0]['output']['replayability'], 'redacted')
            self.assertGreater(trace['privacy']['redacted_field_count'], 0)
            export_bundle(capture.path, root/'export.zip')
            with zipfile.ZipFile(root/'export.zip') as z:
                self.assertNotIn(secret, z.read('trace.json').decode())

    def test_generic_storage_and_custom_capture_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'data.json'
            save(path, {'logs': 'api_key=canary', 'result': {'Authorization': 'canary'}})
            self.assertNotIn('canary', path.read_text())
            with Capture('custom', Path(directory)/'traces', input={'customer_id': 'private-customer'},
                         sanitization_policy='CUSTOM', sensitive_fields=['customer_id']) as capture:
                with capture.span('tool', input={'customer_id': 'private-customer'}) as span:
                    span.set_output({'customer_id': 'private-customer'})
            self.assertNotIn('private-customer', Path(capture.path).read_text())

    def test_environment_header_pairs_malformed_and_no_secret_metadata(self):
        s=Sanitizer()
        data={'environment':{'OPENAI_API_KEY':'synthetic-env-secret','AWS_SECRET_ACCESS_KEY':'synthetic-env-secret'},
              'headers':[{'name':'Authorization','value':'synthetic-env-secret'}],
              'broken':'{"password":"synthetic-env-secret",'}
        self.assertNotIn('synthetic-env-secret',json.dumps(s.sanitize(data)))
        self.assertEqual(s.metadata()['status'],'failed_closed')
        self.assertNotIn('synthetic-env-secret',json.dumps(s.metadata()))

    def test_custom_redactor_failure_does_not_leak(self):
        def fail(value): raise ValueError('raw-secret-do-not-log')
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError,'Custom sanitizer failed'):
                Capture('bad',root,input={},redactor=fail,on_capture_error='raise')
            self.assertFalse(list(Path(root).rglob('*.json')))
