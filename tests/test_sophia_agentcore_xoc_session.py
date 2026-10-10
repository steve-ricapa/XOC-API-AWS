"""El puente de despliegue no cambia de identidad ni persiste credenciales."""
import datetime
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ac2_xoc_session', Path(__file__).parent/'ac1/run_xoc_session.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class XocSessionTests(unittest.TestCase):
    def credentials(self, seconds=900):
        return {'AccessKeyId':'fixture-access', 'SecretAccessKey':'fixture-secret',
                'SessionToken':'fixture-session', 'Expiration':(
                    datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=seconds)).isoformat()}

    def test_only_explicit_profile_and_region(self):
        with patch.object(bridge.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{}')) as run:
            bridge.cli_json(['sts','get-caller-identity'])
        self.assertEqual(run.call_args.args[0],['aws','sts','get-caller-identity','--profile','xoc-dev','--region','us-east-1'])

    def test_other_account_stops_before_credentials(self):
        with patch.object(bridge,'cli_json',return_value={'Account':'000000000000'}) as call:
            with self.assertRaises(RuntimeError): bridge.session_environment()
        self.assertEqual(call.call_count,1)

    def test_inherited_aws_credentials_and_endpoints_removed(self):
        inherited={'AWS_PROFILE':'unrelated','AWS_ACCESS_KEY_ID':'stale',
                   'AWS_ENDPOINT_URL':'https://invalid.example','AWS_WEB_IDENTITY_TOKEN_FILE':'untrusted',
                   'PATH':os.environ.get('PATH','')}
        with patch.dict(os.environ,inherited,clear=True), patch.object(bridge,'cli_json',side_effect=[
                {'Account':bridge.ACCOUNT},self.credentials()]), patch.object(bridge.Path,'exists',return_value=False):
            env=bridge.session_environment()
            self.assertEqual(os.environ['AWS_ACCESS_KEY_ID'],'stale')
        self.assertEqual(env['AWS_ACCESS_KEY_ID'],'fixture-access')
        self.assertEqual(env['AWS_DEFAULT_PROFILE'],'xoc-dev')
        self.assertEqual(env['AWS_REGION'],'us-east-1')
        self.assertEqual(env['AWS_EC2_METADATA_DISABLED'],'true')
        self.assertNotIn('AWS_PROFILE',env)
        self.assertNotIn('AWS_ENDPOINT_URL',env)
        self.assertNotIn('AWS_WEB_IDENTITY_TOKEN_FILE',env)
        self.assertEqual(env['AWS_CONFIG_FILE'],env['AWS_SHARED_CREDENTIALS_FILE'])

    def test_short_session_rejected(self):
        with patch.object(bridge,'cli_json',side_effect=[{'Account':bridge.ACCOUNT},self.credentials(120)]):
            with self.assertRaises(RuntimeError): bridge.session_environment()

    def test_profile_fallback_file_must_not_exist(self):
        with patch.object(bridge,'cli_json',side_effect=[{'Account':bridge.ACCOUNT},self.credentials()]), patch.object(bridge.Path,'exists',return_value=True):
            with self.assertRaises(RuntimeError): bridge.session_environment()

    def test_cli_error_does_not_echo_sensitive_output(self):
        with patch.object(bridge.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='fixture-secret',stderr='fixture-secret')):
            with self.assertRaises(RuntimeError) as caught: bridge.cli_json(['configure','export-credentials'])
        self.assertNotIn('fixture-secret',str(caught.exception))


if __name__ == '__main__': unittest.main()
