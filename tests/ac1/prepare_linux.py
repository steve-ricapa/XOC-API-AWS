"""Build a credential-free Linux context; no host repository/secret mounts."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(root/'tests/ic1/.build'):
        raise ValueError('new evidence context required')
    output.mkdir(parents=True)
    files = list((root/'src').rglob('*.py')) + list((root/'tests').glob('test*.py'))
    files += list((root/'tests/ac1').glob('*.py')) + [root/'tests/ac1/Dockerfile']
    manifest = []
    for path in files:
        if path.is_symlink(): raise ValueError('linked source')
        relative = path.relative_to(root)
        target = output/relative; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        manifest.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(manifest), 'context': str(output)}))


if __name__ == '__main__': main()
