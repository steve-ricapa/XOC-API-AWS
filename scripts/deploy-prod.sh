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

get_stack_status() {
  aws cloudformation list-stacks \
    --stack-status-filter CREATE_IN_PROGRESS CREATE_COMPLETE CREATE_FAILED ROLLBACK_IN_PROGRESS ROLLBACK_COMPLETE UPDATE_IN_PROGRESS UPDATE_COMPLETE UPDATE_ROLLBACK_IN_PROGRESS UPDATE_ROLLBACK_COMPLETE DELETE_IN_PROGRESS DELETE_FAILED \
    --query "StackSummaries[?StackName=='$1'].StackStatus | [0]" \
    --output text 2>/dev/null
}

ensure_stack_absent() {
  local status
  status=$(get_stack_status "$1")
  if [ -n "$status" ] && [ "$status" != "None" ]; then
    fail "El stack $1 ya existe con estado $status. En AWS Academy este script asume despliegue limpio; bórralo antes de reintentar."
  fi
}

wait_for_stack_create() {
  local stack_name="$1"
  while true; do
    local status
    status=$(get_stack_status "$stack_name")
    case "$status" in
      CREATE_COMPLETE)
        ok "Stack $stack_name creado"
        return 0
        ;;
      CREATE_IN_PROGRESS|REVIEW_IN_PROGRESS|None)
        sleep 10
        ;;
      *)
        fail "Stack $stack_name terminó en estado $status"
        ;;
    esac
  done
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
DB_SECRET_ID="xoc/api/prod/database"
SNAPSHOTS_BUCKET_NAME="xoc-prod-snapshots-$AWS_ACCOUNT"
SNAPSHOTS_BUCKET_ARN="arn:aws:s3:::$SNAPSHOTS_BUCKET_NAME"

# Generar secretos
DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# ─────────────────────────────────────────────────────────
# Paso 1: Confirmar configuración demo JWT
# ─────────────────────────────────────────────────────────
step "1 de 8" "Confirmar JWT secret directo en environment"

ok "JWT secret se inyecta desde serverless/stages/prod.yml (sin SSM)"
confirm

# ─────────────────────────────────────────────────────────
# Paso 2: Deploy network (VPC, subnets, NAT, SGs)
# ─────────────────────────────────────────────────────────
step "2 de 8" "Desplegar infraestructura de red (VPC + NAT)"

cd "$SERVICE_DIR"
ensure_stack_absent "$STACK_NETWORK"
aws cloudformation create-stack \
  --stack-name "$STACK_NETWORK" \
  --template-body file://infra/network-prod.yml \
  --capabilities CAPABILITY_IAM > /dev/null
wait_for_stack_create "$STACK_NETWORK"

# Descubrir recursos de red por tags para evitar cloudformation:DescribeStacks
LAMBDA_SUBNET_A=$(aws ec2 describe-subnets --filters Name=tag:Name,Values=xoc-prod-lambda-private-a --query "Subnets[0].SubnetId" --output text)
LAMBDA_SUBNET_B=$(aws ec2 describe-subnets --filters Name=tag:Name,Values=xoc-prod-lambda-private-b --query "Subnets[0].SubnetId" --output text)
DB_SUBNET_A=$(aws ec2 describe-subnets --filters Name=tag:Name,Values=xoc-prod-db-private-a --query "Subnets[0].SubnetId" --output text)
DB_SUBNET_B=$(aws ec2 describe-subnets --filters Name=tag:Name,Values=xoc-prod-db-private-b --query "Subnets[0].SubnetId" --output text)
RDS_SG_ID=$(aws ec2 describe-security-groups --filters Name=tag:Name,Values=xoc-prod-rds-sg --query "SecurityGroups[0].GroupId" --output text)
LAMBDA_SG_ID=$(aws ec2 describe-security-groups --filters Name=tag:Name,Values=xoc-prod-lambda-sg --query "SecurityGroups[0].GroupId" --output text)

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

ensure_stack_absent "$STACK_DATA"
aws cloudformation create-stack \
  --stack-name "$STACK_DATA" \
  --template-body file://infra/data-prod.yml \
  --parameters \
    ParameterKey=DbSubnetAId,ParameterValue="$DB_SUBNET_A" \
    ParameterKey=DbSubnetBId,ParameterValue="$DB_SUBNET_B" \
    ParameterKey=RdsSecurityGroupId,ParameterValue="$RDS_SG_ID" \
    ParameterKey=DatabasePassword,ParameterValue="$DB_PASSWORD" \
  --capabilities CAPABILITY_IAM > /dev/null
wait_for_stack_create "$STACK_DATA"

ok "Database secret id: $DB_SECRET_ID"
confirm

# ─────────────────────────────────────────────────────────
# Paso 4: Deploy storage (S3 snapshots)
# ─────────────────────────────────────────────────────────
step "4 de 8" "Desplegar bucket S3 para snapshots"

ensure_stack_absent "$STACK_STORAGE"
aws cloudformation create-stack \
  --stack-name "$STACK_STORAGE" \
  --template-body file://infra/storage-prod.yml \
  --capabilities CAPABILITY_IAM > /dev/null
wait_for_stack_create "$STACK_STORAGE"
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

export DATABASE_SECRET_ARN="$DB_SECRET_ID"
export SNAPSHOTS_BUCKET_NAME="$SNAPSHOTS_BUCKET_NAME"
export SNAPSHOTS_BUCKET_ARN="$SNAPSHOTS_BUCKET_ARN"
export LAMBDA_SECURITY_GROUP_ID="$LAMBDA_SG_ID"
export LAMBDA_PRIVATE_SUBNET_A_ID="$LAMBDA_SUBNET_A"
export LAMBDA_PRIVATE_SUBNET_B_ID="$LAMBDA_SUBNET_B"

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
    echo "     aws secretsmanager get-secret-value --secret-id $DB_SECRET_ID"
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
echo "    JWT secret:   environment variable JWT_SECRET_KEY"
echo "    DB secret:    $DB_SECRET_ID"
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
