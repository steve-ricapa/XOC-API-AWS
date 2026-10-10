"""Puente local para Serverless 3 (SDK v2) y aws login de xoc-dev.

Obtiene sólo ese perfil mediante AWS CLI. Credenciales sólo en memoria y entorno
del hijo; nunca en archivos, argumentos o stdout. No modifica ~/.aws. No admite
scripts arbitrarios ni fallback a otro perfil. Ejecuta scripts npm existentes.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PROFILE = 'xoc-dev'
REGION = 'us-east-1'
ACCOUNT = '811776156524'


def cli_json(args):
    result = subprocess.run(['aws', *args, '--profile', PROFILE, '--region', REGION],
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError('No se pudo resolver la sesión xoc-dev; salida sensible omitida')
    return json.loads(result.stdout)


def session_environment():
    identity = cli_json(['sts', 'get-caller-identity', '--output', 'json'])
    if identity['Account'] != ACCOUNT:
        raise RuntimeError('Cuenta XOC incorrecta')
    credentials = cli_json(['configure', 'export-credentials', '--format', 'process'])
    expiration = datetime.datetime.fromisoformat(credentials['Expiration'].replace('Z', '+00:00'))
    if (expiration-datetime.datetime.now(datetime.timezone.utc)).total_seconds() < 300:
        raise RuntimeError('La sesión temporal requiere renovación antes del deploy')
    env = {k: v for k, v in os.environ.items() if not k.startswith('AWS_')}
    # SDK v2 cannot parse login_session. Prevent all profile/metadata fallback;
    # the child uses ONLY the credentials just obtained from the xoc-dev profile.
    absent = str(ROOT/'tests/ic1/.build/ac2-real/credential-file-must-not-exist')
    if Path(absent).exists():
        raise RuntimeError('La ruta de aislamiento de perfiles debe estar ausente')
    env.update(AWS_ACCESS_KEY_ID=credentials['AccessKeyId'],
               AWS_SECRET_ACCESS_KEY=credentials['SecretAccessKey'],
               AWS_SESSION_TOKEN=credentials['SessionToken'],
               AWS_CONFIG_FILE=absent, AWS_SHARED_CREDENTIALS_FILE=absent,
               AWS_DEFAULT_PROFILE=PROFILE, AWS_EC2_METADATA_DISABLED='true',
               AWS_REGION=REGION, AWS_DEFAULT_REGION=REGION)
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--script', choices=['deploy:agentcore:prod', 'deploy:chat:prod'], required=True)
    parser.add_argument('--package', type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    if not package.is_relative_to(ROOT/'tests/ic1/.build/ac2-real'):
        raise ValueError('Se requiere un paquete revisado dentro de la evidencia AC-2 real')
    template = json.loads((package/'cloudformation-template-update-stack.json').read_bytes())
    if args.script == 'deploy:agentcore:prod':
        assert template['Resources']['SophiaGateway']['Properties']['Name'] == 'xoc-api-agentcore-prod'
    env = session_environment()
    # Verify the actual credential-consuming SDK, not only the CLI login cache.
    check = subprocess.run(['node', '-e',
        "const AWS=require('aws-sdk');new AWS.STS({region:'us-east-1'}).getCallerIdentity({},(e,d)=>{if(e||d.Account!=='811776156524')process.exit(1);console.log('Identidad SDK verificada: xoc-dev / 8117****6524');});"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    if check.returncode:
        raise RuntimeError('Falló la verificación STS del SDK legado; salida omitida')
    print(check.stdout.strip(), flush=True)
    command = ['npm.cmd' if os.name == 'nt' else 'npm', 'run', args.script, '--', '--package', str(package.relative_to(ROOT))]
    if os.name == 'nt':
        command = ['cmd.exe', '/d', '/c', *command]
    return subprocess.run(command, cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never render subprocess output or credentials on failures.
        print('Puente xoc-dev falló: '+type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
