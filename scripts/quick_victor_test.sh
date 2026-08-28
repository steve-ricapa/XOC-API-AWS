#!/bin/bash
# quick_victor_test.sh - Prueba rápida de VICTOR
# Solo verifica conectividad y assessment básico

set -euo pipefail

VICTOR_URL="${1:-http://10.20.0.22:8000}"
TENANT_ID="${2:-1}"

echo "🔍 Probando VICTOR en: $VICTOR_URL"
echo "   Tenant ID: $TENANT_ID"
echo ""

# 1. Health check
echo "1️⃣ Health Check..."
if curl -s --connect-timeout 5 --max-time 10 "$VICTOR_URL/health" | jq '.'; then
    echo "   ✅ Health OK"
else
    echo "   ❌ Health falló"
    exit 1
fi
echo ""

# 2. Assessment test
echo "2️⃣ Assessment Test..."
TICKET_ID="test-$(date +%s)"
RESPONSE=$(curl -s -w "\n%{http_code}" \
    --connect-timeout 10 \
    --max-time 60 \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{
        \"phase\": \"assessment\",
        \"ticketId\": \"$TICKET_ID\",
        \"tenantId\": $TENANT_ID,
        \"subject\": \"Test: Archivo malicioso\",
        \"description\": \"Prueba de conectividad con VICTOR\"
    }" \
    "$VICTOR_URL/api/agents/VictorDurableAgent/run")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "   ✅ Assessment OK"
    echo "   Response:"
    echo "$BODY" | jq '.'
else
    echo "   ❌ Assessment falló (HTTP $HTTP_CODE)"
    echo "   Response: $BODY"
fi
echo ""

# 3. Plan test
echo "3️⃣ Plan Generation Test..."
RESPONSE=$(curl -s -w "\n%{http_code}" \
    --connect-timeout 10 \
    --max-time 120 \
    -X POST \
    -H "Content-Type: application/json" \
    -d "{
        \"phase\": \"plan\",
        \"ticketId\": \"$TICKET_ID\",
        \"tenantId\": $TENANT_ID,
        \"subject\": \"Test: Archivo malicioso\",
        \"description\": \"Se detectó archivo sospechoso en /tmp/trojan.sh\"
    }" \
    "$VICTOR_URL/api/agents/VictorDurableAgent/run")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "   ✅ Plan OK"
    echo "   Plan:"
    echo "$BODY" | jq '.plan // .'
else
    echo "   ❌ Plan falló (HTTP $HTTP_CODE)"
    echo "   Response: $BODY"
fi
echo ""

echo "=========================================="
echo "  PRUEBA COMPLETADA"
echo "=========================================="