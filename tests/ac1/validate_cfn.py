"""Validate generated resources against a downloaded AWS CFN specification.

Read-only validator, not an IaC deployment mechanism. The spec is a public AWS
data file; save its hash/version with AC-1 evidence, do not fetch it at test time.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    spec_bytes = args.spec.read_bytes(); spec = json.loads(spec_bytes)
    root = Path(__file__).resolve().parents[2]
    resources = json.loads(subprocess.check_output(['node', '-e',
        "console.log(JSON.stringify(require('./serverless.agentcore').resources.Resources))"], cwd=root))
    checked = []

    def check(type_name, value, is_resource=False):
        definition = spec['ResourceTypes' if is_resource else 'PropertyTypes'][type_name]
        props = definition['Properties']
        assert not set(value)-set(props), (type_name, set(value)-set(props))
        assert all(not p.get('Required') or k in value for k, p in props.items()), type_name
        checked.append(type_name)
        prefix = type_name.split('.')[0]
        for key, val in value.items():
            if isinstance(val, dict) and any(k == 'Ref' or k.startswith('Fn::') for k in val): continue
            rule = props[key]
            kind = rule.get('Type')
            if kind in ('List', 'Map') and 'ItemType' in rule:
                for item in (val if kind == 'List' else val.values()):
                    check(prefix+'.'+rule['ItemType'], item)
            elif kind and kind not in ('List', 'Map'):
                check(prefix+'.'+kind, val)

    for resource in resources.values():
        check(resource['Type'], resource['Properties'], True)
    report = {'passed': True, 'spec_version': spec['ResourceSpecificationVersion'],
              'spec_sha256': hashlib.sha256(spec_bytes).hexdigest(), 'checked': checked}
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__': main()
