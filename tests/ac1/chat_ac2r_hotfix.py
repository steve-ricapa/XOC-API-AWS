"""Isolated AC2R repair; preserve prior artifacts and require exact live baseline."""
import argparse
import hashlib
import json
import shutil
import zipfile

import chat_ac2r_release as release

PREVIOUS = release.OUT
OUT = PREVIOUS / 'repair-20261012'
EXPECTED = 'u+j2IEXmhHNjFVJDKv60PFvnJDdaVF5pBcpYNiQmNDw='
FILES = ('src/shared/sophia_agentcore_client.py', 'src/shared/sophia_tool_chat.py')


def prepare():
    live = release.cli_json(['lambda', 'get-function-configuration', '--function-name', 'xoc-api-chat-prod-chatAgentsApi'])
    assert live['CodeSha256'] == EXPECTED, 'Unexpected live code'
    assert live['Environment']['Variables']['SOPHIA_TOOL_EXECUTION'] == 'agentcore'
    OUT.mkdir(exist_ok=False)
    for name in ('chat-base.zip', 'authenticated-disabled-smoke.json'):
        shutil.copyfile(PREVIOUS / name, OUT / name)
    manifest = json.loads((PREVIOUS / 'chat-artifact-manifest.json').read_bytes())
    with zipfile.ZipFile(PREVIOUS / 'chat-isolated.zip') as old, zipfile.ZipFile(OUT / 'chat-isolated.zip', 'w', zipfile.ZIP_DEFLATED) as new:
        for info in old.infolist():
            data = (release.RELEASE / info.filename).read_bytes() if info.filename in FILES else old.read(info.filename)
            new.writestr(info, data)
            if info.filename in FILES:
                manifest['overlay'][info.filename] = hashlib.sha256(data).hexdigest()
    manifest['previous_code_sha256'] = EXPECTED
    manifest['repair_files'] = list(FILES)
    manifest['artifact_sha256'] = hashlib.sha256((OUT / 'chat-isolated.zip').read_bytes()).hexdigest()
    (OUT / 'chat-artifact-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('PREPARED_ISOLATED_REPAIR')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'package', 'inspect', 'deploy'])
    parser.add_argument('--mode', choices=['agentcore', 'disabled'], default='agentcore')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    else:
        release.OUT = OUT
        release.BASE_SHA = EXPECTED
        if args.action == 'package':
            raise SystemExit(release.package(args.mode))
        elif args.action == 'inspect':
            release.inspect(args.mode)
        else:
            # Serverless 3 checks this generated state even with --package.
            # Restore only the selected, reviewed state; never package concurrently.
            state = OUT / ('chat-' + args.mode) / 'serverless-state.json'
            target = release.RELEASE / '.serverless'
            target.mkdir(exist_ok=True)
            shutil.copyfile(state, target / 'serverless-state.json')
            raise SystemExit(release.deploy(args.mode))
