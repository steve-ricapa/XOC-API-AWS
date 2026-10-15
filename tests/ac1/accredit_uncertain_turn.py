"""Accredit the colleague's live Chat hotfix before a code-only overlay."""
import base64
import hashlib
import io
import json
import shutil
import subprocess
import urllib.request
import zipfile
from chat_ac2r_release import ROOT, OUT, cli_json

SHA = '6hafc+Evju/duP+CKsOUwZnkZZzXDbOqiSRIS2Vucu4='
FILE = 'src/handlers/routes/chat.py'


def main():
    assert cli_json(['sts', 'get-caller-identity'])['Account'] == '811776156524'
    live = cli_json(['lambda', 'get-function', '--function-name', 'xoc-api-chat-prod-chatAgentsApi'])
    assert live['Configuration']['CodeSha256'] == SHA
    with urllib.request.urlopen(live['Code']['Location'], timeout=45) as response:
        raw = response.read(150000001)
    assert base64.b64encode(hashlib.sha256(raw).digest()).decode() == SHA
    previous = OUT / 'timeout26-20261013'
    with zipfile.ZipFile(previous / 'chat-isolated.zip') as old, zipfile.ZipFile(io.BytesIO(raw)) as current:
        assert old.namelist() == current.namelist()
        assert [n for n in old.namelist() if old.read(n) != current.read(n)] == [FILE]
        expected = old.read(FILE).decode().replace('\r\n', '\n')
        source = subprocess.check_output(['git', 'show', '3cf7af5:' + FILE], cwd=ROOT).decode()
        helper = source[source.index('def _uncertain_sophia_turn():'):source.index('_AFFINITY_COOKIE_KEYS')]
        expected = expected.replace('_AFFINITY_COOKIE_KEYS', helper + '_AFFINITY_COOKIE_KEYS', 1)
        expected = expected.replace('logger.warning("[chat] SOPHIA timeout after %ss", timeout_seconds)\n',
            'logger.warning("[chat] SOPHIA timeout after %ss", timeout_seconds)\n            if governed:\n                raise _uncertain_sophia_turn() from None\n', 1)
        expected = expected.replace('raise sophia_tool_chat.protocol_error("sophia_runtime_unavailable") from None',
                                    'raise _uncertain_sophia_turn() from None', 1)
        expected = expected.replace('# No raw provider errors, legacy retries or alternative execution.\n',
            '# No raw provider errors, legacy retries or alternative execution.\n            if sophia_response.status_code == 409 or sophia_response.status_code >= 500:\n                raise _uncertain_sophia_turn()\n', 1)
        assert current.read(FILE).decode().replace('\r\n', '\n') == expected
    destination = OUT / 'uncertain-turn-accredited-20261015'
    destination.mkdir(exist_ok=False)
    for name in ('chat-base.zip', 'authenticated-disabled-smoke.json'):
        shutil.copyfile(previous / name, destination / name)
    (destination / 'chat-isolated.zip').write_bytes(raw)
    manifest = json.loads((previous / 'chat-artifact-manifest.json').read_bytes())
    manifest.update(artifact_sha256=hashlib.sha256(raw).hexdigest(),
                    accredited_commits=['71aa494', '3cf7af5'], source_base='9e70854')
    manifest['overlay'][FILE] = hashlib.sha256(expected.encode()).hexdigest()
    (destination / 'chat-artifact-manifest.json').write_text(json.dumps(manifest, indent=2))
    print('LIVE_HOTFIX_ACCREDITED_ONLY_CHAT_CHANGED')


if __name__ == '__main__':
    main()
