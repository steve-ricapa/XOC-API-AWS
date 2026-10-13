"""Single-file Chat correction over the accredited deployed AC2R artifact."""
import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import zipfile

import chat_ac2r_release as release

PREVIOUS = release.OUT / 'repair-20261012'
OUT = release.OUT / 'continuity-20261013'
EXPECTED = 'VKixlT8FOnKMHQ44upO2EIPeeaQ6dEltWnbzT5zAy70='
FILE = 'src/handlers/routes/chat.py'


def prepare():
    assert release.cli_json(['sts', 'get-caller-identity'])['Account'] == '811776156524'
    live = release.cli_json(['lambda', 'get-function-configuration', '--function-name', 'xoc-api-chat-prod-chatAgentsApi'])
    assert live['CodeSha256'] == EXPECTED
    assert live['Environment']['Variables']['SOPHIA_TOOL_EXECUTION'] == 'agentcore'
    raw = (PREVIOUS / 'chat-isolated.zip').read_bytes()
    assert base64.b64encode(hashlib.sha256(raw).digest()).decode() == EXPECTED
    OUT.mkdir(exist_ok=False)
    for name in ('chat-base.zip', 'authenticated-disabled-smoke.json'):
        shutil.copyfile(PREVIOUS / name, OUT / name)
    manifest = json.loads((PREVIOUS / 'chat-artifact-manifest.json').read_bytes())
    data = (release.RELEASE / FILE).read_bytes()
    with zipfile.ZipFile(PREVIOUS / 'chat-isolated.zip') as old, zipfile.ZipFile(OUT / 'chat-isolated.zip', 'w', zipfile.ZIP_DEFLATED) as new:
        assert old.namelist().count(FILE) == 1
        for info in old.infolist():
            new.writestr(info, data if info.filename == FILE else old.read(info.filename))
    with zipfile.ZipFile(PREVIOUS / 'chat-isolated.zip') as old, zipfile.ZipFile(OUT / 'chat-isolated.zip') as new:
        assert old.namelist() == new.namelist()
        assert [name for name in old.namelist() if old.read(name) != new.read(name)] == [FILE]
    manifest.update(previous_code_sha256=EXPECTED, repair_files=[FILE],
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=release.RELEASE, text=True).strip(),
        artifact_sha256=hashlib.sha256((OUT / 'chat-isolated.zip').read_bytes()).hexdigest())
    manifest['overlay'][FILE] = hashlib.sha256(data).hexdigest()
    (OUT / 'chat-artifact-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('PREPARED_SINGLE_FILE_CONTINUITY_FIX')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'package', 'inspect', 'deploy'])
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    else:
        release.OUT, release.BASE_SHA = OUT, EXPECTED
        if args.action == 'package':
            raise SystemExit(release.package('agentcore'))
        elif args.action == 'inspect':
            release.inspect('agentcore')
            report = json.loads((OUT / 'chat-agentcore-inspection.json').read_bytes())
            assert report['environment_diff'] == [], 'Unexpected configuration drift'
            assert report['lambda_property_diff'] == ['Code']
        else:
            report = json.loads((OUT / 'chat-agentcore-inspection.json').read_bytes())
            assert report['environment_diff'] == []
            target = release.RELEASE / '.serverless'
            target.mkdir(exist_ok=True)
            shutil.copyfile(OUT / 'chat-agentcore/serverless-state.json', target / 'serverless-state.json')
            raise SystemExit(release.deploy('agentcore'))
