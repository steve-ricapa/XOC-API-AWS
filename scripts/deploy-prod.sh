#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────
# XOC - Deploy producción completo
# Uso:  bash scripts/deploy-prod.sh
# Requiere: aws CLI, serverless, python3
# ─────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }
step()  { echo ""; echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"; echo -e "${CYAN}  Paso $1: $2${NC}"; echo -e "${CYAN}════════════════════════════════════════════════════════════${NC}"; }

confirm() {
  echo ""
  read -rp "  ¿Continuar? [S/n]: " yn
  case "$yn" in [nN]*) exit 0 ;; esac
}

# ── Preliminares ────────────────────────────────────────

info "Verificando herramientas..."
command -v aws       >/dev/null 2>&1 || fail "aws CLI no encontrado. Instálalo y configura las credenciales."
command -v serverless >/dev/null 2>&1 || fail "serverless no encontrado. Instálalo: npm i -g serverless"
command -v python3   >/dev/null 2>&1 || fail "python3 no encontrado."

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) || fail "No se pudo autenticar con AWS. Revisa tus credenciales."
AWS_REGION=$(aws configure get region 2>/dev/null || echo "us-east-1")
ok "Cuenta AWS: $AWS_ACCOUNT | Región: $AWS_REGION"

# ── Variables ────────────────────────────────────────────

STACK_NETWORK="xoc-infra-network-prod"
STACK_DATA="xoc-infra-data-prod"
STACK_STORAGE="xoc-infra-storage-prod"
SERVICE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Generar secretos
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# ─────────────────────────────────────────────────────────
# Paso 1: JWT secret en SSM
# ─────────────────────────────────────────────────────────
step "1 de 8" "Crear JWT secret en SSM Parameter Store"

aws ssm put-parameter \
  --name "/xoc/api/prod/jwt-secret-key" \
  --type "SecureString" \
  --value "$JWT_SECRET" \
  --overwrite > /dev/null

ok "SSM parameter /xoc/api/prod/jwt-secret-key creado"
confirm

# ─────────────────────────────────────────────────────────
# Paso 2: Deploy network (VPC, subnets, NAT, SGs)
# ─────────────────────────────────────────────────────────
step "2 de 8" "Desplegar infraestructura de red (VPC + NAT)"

cd "$SERVICE_DIR"
aws cloudformation deploy \
  --stack-name "$STACK_NETWORK" \
  --template-file infra/network-prod.yml \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset

ok "Stack $STACK_NETWORK desplegado"

# Extraer outputs de red
NET_OUTPUTS=$(aws cloudformation describe-stacks --stack-name "$STACK_NETWORK" --query "Stacks[0].Outputs" --output json)

get_net_output() {
  echo "$NET_OUTPUTS" | python3 -c "import sys,json; outs=json.load(sys.stdin); print([o['OutputValue'] for o in outs if o['OutputKey']=='$1'][0])"
}

LAMBDA_SUBNET_A=$(get_net_output LambdaPrivateSubnetAId)
LAMBDA_SUBNET_B=$(get_net_output LambdaPrivateSubnetBId)
DB_SUBNET_A=$(get_net_output DbPrivateSubnetAId)
DB_SUBNET_B=$(get_net_output DbPrivateSubnetBId)
RDS_SG_ID=$(get_net_output RdsSecurityGroupId)
LAMBDA_SG_ID=$(get_net_output LambdaSecurityGroupId)

ok "Outputs de red capturados"
echo "  LambdaPrivateSubnetA: $LAMBDA_SUBNET_A"
echo "  LambdaPrivateSubnetB: $LAMBDA_SUBNET_B"
echo "  DbPrivateSubnetA:     $DB_SUBNET_A"
echo "  DbPrivateSubnetB:     $DB_SUBNET_B"
echo "  RdsSecurityGroupId:   $RDS_SG_ID"
echo "  LambdaSecurityGroupId: $LAMBDA_SG_ID"
confirm

# ─────────────────────────────────────────────────────────
# Paso 3: Deploy data (RDS + Secrets Manager)
# ─────────────────────────────────────────────────────────
step "3 de 8" "Desplegar base de datos RDS PostgreSQL"

aws cloudformation deploy \
  --stack-name "$STACK_DATA" \
  --template-file infra/data-prod.yml \
  --parameter-overrides \
    DbSubnetAId="$DB_SUBNET_A" \
    DbSubnetBId="$DB_SUBNET_B" \
    RdsSecurityGroupId="$RDS_SG_ID" \
    DatabasePassword="$DB_PASSWORD" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset

ok "Stack $STACK_DATA desplegado"

DATA_OUTPUTS=$(aws cloudformation describe-stacks --stack-name "$STACK_DATA" --query "Stacks[0].Outputs" --output json)
DB_SECRET_ARN=$(echo "$DATA_OUTPUTS" | python3 -c "import sys,json; outs=json.load(sys.stdin); print([o['OutputValue'] for o in outs if o['OutputKey']=='DatabaseSecretArn'][0])")

ok "DatabaseSecretArn: $DB_SECRET_ARN"
confirm

# ─────────────────────────────────────────────────────────
# Paso 4: Deploy storage (S3 snapshots)
# ─────────────────────────────────────────────────────────
step "4 de 8" "Desplegar bucket S3 para snapshots"

aws cloudformation deploy \
  --stack-name "$STACK_STORAGE" \
  --template-file infra/storage-prod.yml \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset

ok "Stack $STACK_STORAGE desplegado"
confirm

# ─────────────────────────────────────────────────────────
# Paso 5: Configurar CORS
# ─────────────────────────────────────────────────────────
step "5 de 8" "Configurar dominio CORS del frontend"

CORS_FILE="$SERVICE_DIR/serverless/stages/prod.yml"
echo ""
echo "  Ahora edita el archivo:"
echo "    $CORS_FILE"
echo ""
echo "  Busca 'https://api.example.com' y reemplázalo por el"
echo "  dominio real de tu frontend (ej: https://app.xoc.com)."
echo "  Si no lo sabes aún, pon '*' (no seguro) o déjalo así"
echo "  y cámbialo después."
echo ""

read -rp "  Presiona Enter después de editar el archivo (o escribe 'skip' para saltar): " cors_confirm
if [ "$cors_confirm" != "skip" ]; then
  ok "CORS configurado"
fi

# ─────────────────────────────────────────────────────────
# Paso 6: Deploy backend (Serverless Framework)
# ─────────────────────────────────────────────────────────
step "6 de 8" "Desplegar backend con Serverless Framework"

cd "$SERVICE_DIR"

# Validar que serverless tiene sesión
serverless info --stage prod 2>/dev/null || true

info "Ejecutando: serverless deploy --stage prod"
serverless deploy --stage prod

ok "Backend desplegado"
confirm

# ─────────────────────────────────────────────────────────
# Paso 7: Bootstrap del esquema DB
# ─────────────────────────────────────────────────────────
step "7 de 8" "Bootstrap del esquema en RDS"

echo ""
echo "  ¿Quieres crear las tablas en la base de datos ahora?"
echo ""
echo "  ⚠️  La RDS está en VPC privada. Solo puedes bootstrapear"
echo "     desde un recurso dentro de la VPC (bastion, otra Lambda,"
echo "     o conexión VPN)."
echo ""
echo "  Opciones:"
echo "    1) Invocar la Lambda apiHttp forzando bootstrap"
echo "    2) Saltar (lo haré manual desde bastion/VPN después)"
echo ""

read -rp "  Elige [1/2] (default 2): " bootstrap_choice

case "${bootstrap_choice:-2}" in
  1)
    info "Invocando apiHttp en modo bootstrap..."
    aws lambda invoke \
      --function-name "xoc-api-core-prod-api" \
      --invocation-type RequestResponse \
      --payload '{"path":"/health","httpMethod":"GET","headers":{"x-bootstrap":"true"}}' \
      /tmp/bootstrap-response.json 2>&1 || warn "No se pudo invocar. Bootstrap manual necesario."
    ok "Respuesta: $(cat /tmp/bootstrap-response.json)"
    ;;
  *)
    ok "Bootstrap omitido. Para hacerlo manual:"
    echo ""
    echo "  1. Conéctate a un recurso dentro de la VPC (bastion)"
    echo "     o usa SSM Port Forwarding."
    echo ""
    echo "  2. Obtén la DB URL del Secrets Manager:"
    echo "     aws secretsmanager get-secret-value --secret-id $DB_SECRET_ARN"
    echo ""
    echo "  3. Ejecuta:"
    echo "     DATABASE_URL='<postgresql+psycopg2://...>' python3 scripts/bootstrap_schema.py"
    echo ""
    ;;
esac

confirm

# ─────────────────────────────────────────────────────────
# Paso 8: Smoke tests
# ─────────────────────────────────────────────────────────
step "8 de 8" "Smoke tests opcionales"

echo ""
echo "  ¿Quieres ejecutar smoke tests contra el endpoint?"
echo "  Necesitas la URL del API Gateway."
echo ""
read -rp "  API Gateway URL (ej: https://abc123.execute-api.us-east-1.amazonaws.com) [Enter para saltar]: " API_URL

if [ -n "$API_URL" ]; then
  info "Probando health..."
  curl -sf "$API_URL/health" && ok "Health OK" || warn "Health falló"

  info "Probando login..."
  LOGIN_RESP=$(curl -sf -X POST "$API_URL/api/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@xoc.com","password":"test1234"}' 2>/dev/null || true)
  if [ -n "$LOGIN_RESP" ]; then
    ok "Login endpoint responde"
  else
    warn "Login falló (esperado si no hay datos seed)"
  fi

  info "Probando chat..."
  curl -sf -X POST "$API_URL/api/chat" \
    -H "Content-Type: application/json" \
    -d '{"message":"hello"}' > /dev/null 2>&1 && ok "Chat OK" || warn "Chat falló"

  ok "Smoke tests completados"
else
  ok "Smoke tests omitidos"
fi

# ── Fin ──────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ¡Deploy completado!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
echo "  Resumen:"
echo "    JWT secret:   SSM /xoc/api/prod/jwt-secret-key"
echo "    DB secret:    $DB_SECRET_ARN"
echo "    Region:       $AWS_REGION"
echo ""
echo "  ⚠️  La DB password está en Secrets Manager. No la compartas."
echo ""
echo "  Próximos pasos recomendados:"
echo "    1. Verificar que la RDS no sea pública"
echo "    2. Verificar que apiHttp esté en subnets privadas"
echo "    3. Verificar que jwtAuthorizer esté fuera de VPC"
echo "    4. Configurar dominio custom en API Gateway si aplica"
echo ""
