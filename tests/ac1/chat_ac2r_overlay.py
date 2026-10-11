"""Overlay mecánico y allowlisted sobre la base Chat acreditada, no HEAD completo."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT.parent / 'XOC-API-AWS-CHAT-AC2R'
BASE = 'fe3b25f13833fb300ad1049b0e7974b37be8e706'
SOURCE = 'b3544afa4dd12e39f0aa90c26016a2049b5dd337'
FILES = [
    'src/handlers/routes/chat.py',
    'src/shared/sophia_tool_chat.py', 'src/shared/sophia_thread_operations.py',
    'src/shared/sophia_tool_execution.py', 'src/shared/sophia_agentcore_client.py',
    'src/shared/agentcore_context.py', 'src/tool_gateway/policy.py',
    'tests/test_sophia_phase_d.py',
]


def main():
    if subprocess.check_output(['git', '-C', str(DEST), 'rev-parse', 'HEAD'], text=True).strip() != BASE:
        raise ValueError('Se requiere worktree nuevo en la base acreditada')
    if subprocess.check_output(['git', '-C', str(DEST), 'status', '--porcelain'], text=True).strip():
        raise ValueError('No se sobrescriben cambios de un worktree sucio')
    manifest = {'base_commit': BASE, 'source_commit': SOURCE, 'files': {}}
    for name in FILES:
        data = subprocess.check_output(['git', '-C', str(ROOT), 'show', SOURCE + ':' + name])
        target = DEST / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest['files'][name] = {'source_commit': SOURCE, 'sha256': hashlib.sha256(data).hexdigest()}
    evidence = ROOT / 'tests/ic1/.build/ac2r/overlay-origin.json'
    evidence.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
