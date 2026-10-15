"""The production Chat template must retain the live governed Sophia route."""
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _chat_environment(stage):
    result = subprocess.run(
        ['node', '-e', "const c=require('./serverless.chat');"
         "console.log(JSON.stringify(c.functions.chatAgentsApi.environment))"],
        cwd=ROOT, env={**os.environ, 'STAGE': stage},
        capture_output=True, text=True, check=True,
    )
    import json
    return json.loads(result.stdout)


def test_prod_template_retains_governed_route():
    assert _chat_environment('prod')['SOPHIA_GOVERNED_BASE_URL'] == (
        "${env:SOPHIA_GOVERNED_BASE_URL, 'https://prod-a-xoc-sophia.azurewebsites.net'}"
    )


def test_non_prod_template_does_not_force_prod_route():
    assert 'SOPHIA_GOVERNED_BASE_URL' not in _chat_environment('dev')
