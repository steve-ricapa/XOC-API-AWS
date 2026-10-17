"""Local heuristic scan; reports locations/categories, NEVER matching values."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import zipfile

HIGH = {
    'aws_access_key_literal': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'private_key_block': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'jwt_literal': re.compile(r'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{20,}'),
    'sas_signature_value': re.compile(r'[?&]sig=[A-Za-z0-9%+/=]{25,}'),
    'connection_secret_value': re.compile(r'(?:AccountKey|SharedAccessSignature)=[A-Za-z0-9+/=]{25,}',re.I),
}
WORDS = re.compile(r'AWS_ACCESS_KEY|SECRET_ACCESS|Authorization\s*:|Bearer|client_secret|connection_string|password\s*=|refresh_token|access_token|\bSAS\b|SharedAccessSignature',re.I)
AUTHOR = re.compile(r'[Cc]:[\\/]+Users[\\/]+|OneDrive[\\/]+Escritorio')
BAD_SUFFIX = {'.pem','.key','.pfx','.p12'}
EXCLUDE = {'.venv','__pycache__','.pytest_cache','.mypy_cache','.git','build','dist','l2-runs','l3-runs','l4-runs','l6-runs'}

def inspect_text(label, raw, findings, keywords, author):
    try: text=raw.decode('utf-8-sig')
    except UnicodeDecodeError: return
    for number,line in enumerate(text.splitlines(),1):
        for kind,pattern in HIGH.items():
            if pattern.search(line): findings.append(dict(path=label,line=number,type=kind,action='manual_review_required'))
        if WORDS.search(line): keywords.append(dict(path=label,line=number,type='security_keyword_reference'))
        if AUTHOR.search(line): author.append(dict(path=label,line=number))

def scan(root):
    findings, keywords, author, files = [],[],[],[]
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if any(p in EXCLUDE for p in relative.parts) or not path.is_file(): continue
        name=relative.as_posix()
        if path.is_symlink():
            findings.append(dict(path=name,type='symlink',action='exclude'));continue
        files.append(name)
        lower=path.name.lower()
        if path.suffix.lower() in BAD_SUFFIX or lower.startswith(('.env','local.settings','credentials','secrets')):
            findings.append(dict(path=name,type='sensitive_filename',action='exclude_or_review'))
        if path.suffix=='.whl':
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.file_size>8*1024*1024: raise ValueError('Oversize wheel member')
                    inspect_text(name+'!'+member.filename,archive.read(member),findings,keywords,author)
        else: inspect_text(name,path.read_bytes(),findings,keywords,author)
    return dict(schema_version=1, scanned_files=len(files), high_risk_findings=findings,
                keyword_locations=keywords, author_paths=author,
                public_fixture='ES256 scalar=1 in signing fixture: TEST FIXTURE ONLY / PUBLIC / NOT A SECRET / DO NOT USE IN PRODUCTION',
                limitation='Heuristic scan, not proof of absolute absence of secrets. Keyword references need review; values are never emitted.')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=scan(args.root.resolve())
    with args.output.open('x',encoding='utf-8') as stream: json.dump(result,stream,indent=2)
    print(json.dumps(dict(files=result['scanned_files'],high_risk=len(result['high_risk_findings']),keywords=len(result['keyword_locations']),author_paths=len(result['author_paths']))))
    return int(bool(result['high_risk_findings'] or result['author_paths']))

if __name__=='__main__': raise SystemExit(main())
