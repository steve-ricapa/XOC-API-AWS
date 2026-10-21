# Runbook: tenant + servidor de pruebas con gobernanza

Objetivo: crear un tenant aislado en XOC con un servidor EC2 de laboratorio
lleno de artefactos maliciosos simulados, generar tickets con multiples planes
y validar la gobernanza por rol:

| Rol | Aprueba |
| --- | --- |
| `USER` | solo riesgo `basic` |
| `ADMIN` (admin del tenant) | `basic`, `controlled`, `risky` |
| `ADMIN_XOC` / `SUPERADMIN` | todo, incluido `critical` |

Contrato real: `src/shared/risk_config.py`.

## Importante: que es automatico y que no

- El ticket se genera por `POST /tickets`, por el agente (`POST /tickets/agent`) o confirmando una propuesta de SOPHIA en el chat.
- Los scans/findings **no** crean tickets automaticamente hoy.
- Cuando el ticket entra al workflow, el backend llama a VICTOR y pide **multiples planes** (`generatePlans`). Con el backend nuevo (`God_Plansv1`) la respuesta `plans[]` de VICTOR es autoritativa.
- Topologia del laboratorio: VICTOR on-premise (server + executor) se instala **dentro de la misma EC2 de pruebas**. El executor corre en esa EC2 (contenedor con `/opt/xoc-test` montado), asi que los planes actuan sobre los archivos sembrados ahi. La URL de VICTOR del tenant se guarda en `tenant_runtime_settings.function_base_url` y **tiene prioridad sobre la URL global**; los demas tenants siguen usando `AGENTS_FUNCTION_BASE_URL_VICTOR`.
- Sin VICTOR alcanzable el ticket termina en `DERIVADO` y no hay `pending_decision` para aprobar.

## Paso 0 - Backend al dia

Requiere el commit `e1b1ffb` + el cambio de precedencia tenant-first (runtime settings del tenant ganan sobre la URL global de VICTOR).
En la EC2 de deploy:

```bash
cd ~/XOC_AWS && git pull origin main
npm run deploy:automation:prod
npm run deploy:tickets:prod
```

## Paso 1 - Crear la EC2 de pruebas (consola AWS)

Plantilla: `infra/test-server-prod.yml`

1. Consola AWS -> CloudFormation -> Create stack -> With new resources -> Upload a template file.
2. Parametros:
   - `InstanceName`: `xoc-test-target`
   - `VpcId`: `xoc-prod-vpc` (10.20.0.0/16)
   - `SubnetId`: `xoc-prod-public-a` (10.20.0.0/24)
   - `KeyName`: vacio (acceso solo por SSM)
   - `InstanceType`: `t3.small` (minimo recomendado: compila la imagen de VICTOR)
   - `VictorCidr`: `10.20.0.0/16` (o `10.20.0.22/32`)
   - `AllocateElasticIp`: `true` (IP publica fija)
   - `BootstrapScriptUrl`: dejar default (raw GitHub `main`)
   - `InstallVictor`: `true`
   - `VictorRepoRef`: `main`
   - `AnthropicApiKey`: (NoEcho) la misma key que usa VICTOR on-prem; si la dejas vacia, completala luego por SSM en `/opt/victor/.env`
   - `AzureEndpoint` / `AnthropicModel` / `AzureDeployment`: dejar defaults salvo que tu deployment use otro nombre
3. La plantilla crea: IAM role con `AmazonSSMManagedInstanceCore`, SG (SSH/ICMP/8000 solo desde la red de VICTOR, egress total), EC2 Ubuntu 24.04 y EIP. El UserData siembra los artefactos e instala Docker + VICTOR (`/opt/victor`, `docker compose up -d --build`).
4. Cuando el stack este `CREATE_COMPLETE`, anota `PrivateIp`, `PublicIp`, `VictorBaseUrl` e `InstanceId`. El build de VICTOR puede tardar varios minutos despues de que la instancia este `running`; espera `/opt/victor/READY`.

### Verificar sin SSH (desde AWS CloudShell)

```bash
aws ssm describe-instance-information \
  --filters "Key=tag:xoc:role,Values=test-target" \
  --query "InstanceInformationList[].{id:InstanceId,ping:PingStatus}" --output table

# Ver artefactos sembrados por UserData
aws ssm send-command --document-name AWS-RunShellScript \
  --targets "Key=tag:xoc:role,Values=test-target" \
  --parameters '{"commands":["sudo /opt/xoc-test/seed.sh --verify"]}' \
  --query "Command.CommandId" --output text
```

Si el UserData no pudo descargar el seed, se crea un seed minimo (EICAR +
`backdoor.sh`). Para sembrar el set completo:

```bash
aws ssm send-command --document-name AWS-RunShellScript \
  --targets "Key=tag:xoc:role,Values=test-target" \
  --parameters '{"commands":["sudo curl -fsSL https://raw.githubusercontent.com/steve-ricapa/XOC-API-AWS/main/scripts/test-server-seed.sh -o /opt/xoc-test/seed.sh && sudo chmod +x /opt/xoc-test/seed.sh && sudo /opt/xoc-test/seed.sh"]}' \
  --query "Command.CommandId" --output text
```

El seed crea en `/opt/xoc-test/incident/`: EICAR, backdoor.sh, reverse_shell.py,
webshell.php, crypto_miner.sh, persistence.cron, llave SSH falsa, binario
disfrazado `kworker`, SUID simulado y `manifest.json` con hashes.

### Conectividad con VICTOR

- VICTOR corre en la misma EC2: `http://<PrivateIp>:8000` (contenedor `victor-server`).
- El executor corre como contenedor en esa EC2 y tiene montado `/opt/xoc-test`, por eso ve los artefactos del seed.
- El SG permite `8000/tcp` desde `VictorCidr` (las Lambdas de XOC viven en `10.20.0.0/24`).
- Verifica salud sin SSH:

```bash
aws ssm send-command --document-name AWS-RunShellScript \
  --targets "Key=tag:xoc:role,Values=test-target" \
  --parameters '{"commands":["curl -fsS http://127.0.0.1:8000/health","cd /opt/victor && sudo docker compose ps"]}' \
  --query "Command.CommandId" --output text
```

- La URL global `AGENTS_FUNCTION_BASE_URL_VICTOR` (prod: `http://10.20.0.22:8000`) sigue aplicando a los demas tenants. Este tenant usa su runtime setting local.

## Paso 2 - Crear tenant, usuarios e integracion

Desde AWS CloudShell (o cualquier shell con `curl` y `python3`):

```bash
cd ~
git clone https://github.com/steve-ricapa/XOC-API-AWS.git xoc-api && cd xoc-api

export SUPERADMIN_EMAIL="tu-superadmin@xoc.app"
export SUPERADMIN_PASSWORD="********"
export SERVER_PRIVATE_IP="10.20.0.xxx"   # salida del stack
export SERVER_PUBLIC_IP="54.xxx.xxx.xxx" # salida del stack

./scripts/create_test_tenant.sh
```

Crea (idempotente):

- Tenant `XOC Test Lab` (`ACTIVE`);
- `xoc.operator@xoc.local` (`ADMIN_XOC`, global);
- `admin.lab@xoc.local` (`ADMIN` del tenant);
- `analyst.lab@xoc.local` (`USER` del tenant);
- Integracion `xoc_appliance` + `System` apuntando a la EC2;
- Runtime settings del tenant con `function_base_url=http://<PrivateIp>:8000` (VICTOR local);
- Agent key `nmap` para ingesta de scans.

Guarda credenciales e IDs en `~/.xoc/test-tenant.env` (chmod 600).

Notas:
- En prod la creacion de tenants esta cerrada (`PUBLIC_REGISTRATION_ENABLED=false`), por eso se usa el token SUPERADMIN.
- Si los usuarios ya existen con otra password, exporta `ADMIN_PASSWORD`, `USER_PASSWORD` y `OPERATOR_PASSWORD` con la real.
- Si no quieres tocar runtime settings, exporta `SET_VICTOR_RUNTIME_SETTINGS=false`; en ese caso el tenant usara el VICTOR global (y los planes NO se ejecutaran en la EC2 de pruebas).
- `function_base_url` tambien la usa SOPHIA para este tenant; el lab no usa el chat.

## Paso 3 - Tickets y planes multiples

Dry-run (crea 3 tickets y espera los planes, no aprueba):

```bash
./scripts/demo_governance_matrix.sh --env-file ~/.xoc/test-tenant.env
```

El script imprime, por ticket: `risk`, rol requerido, numero de opciones y los
titulos de cada plan. Con el backend nuevo VICTOR devuelve varios planes y el
frontend los muestra en la pestana "Plan" y en el panel de decision.

Aprobacion real (prueba 403 con rol insuficiente y 200 con el correcto):

```bash
./scripts/demo_governance_matrix.sh --env-file ~/.xoc/test-tenant.env --execute
```

Tambien puedes crear tickets manualmente:

```bash
API=https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin.lab@xoc.local","password":"XocLab2026!"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST $API/tickets -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "subject": "Erradicar backdoor y purgar artefactos",
  "description": "Backdoor y multiples archivos maliciosos detectados en /opt/xoc-test/incident del servidor de pruebas. Se requiere eliminar, borrar llaves SSH no autorizadas y purgar el directorio.",
  "priority": "high"
}'
```

## Paso 4 - Verificar en la plataforma

1. Login en https://xoc-frontend.firebaseapp.com/dashboard con cada usuario.
2. Tickets: el ticket pasa `PENDING -> PREAPROBADO` con N opciones de plan.
3. Con `analyst.lab` (USER): solo puede aprobar tickets `basic`; en los demas ve el badge de rol requerido y recibe 403.
4. Con `admin.lab` (ADMIN): aprueba `controlled`/`risky`, no `critical`.
5. Con `xoc.operator` (ADMIN_XOC): aprueba todo, incluido `critical`.
6. Tras aprobar: `APROBADO -> EN_EJECUCION -> RESUELTO/FALLIDO` (la ejecucion la hace VICTOR).

## Troubleshooting

| Sintoma | Causa probable | Accion |
| --- | --- | --- |
| `Tenant already exists` | Ya corriste el script | Es idempotente: reutiliza el tenant |
| Login superadmin falla | Credenciales incorrectas | Usa las reales o crea el tenant desde la UI superadmin |
| Ticket queda `DERIVADO` | VICTOR no configurado/alcanzable/timeout | Revisa runtime settings del tenant, `GET /tenant/runtime-settings`, SG puerto 8000 y `/health` |
| `pending_decision` sin `options` | VICTOR no devolvio `plans[]` | Backend viejo o respuesta legacy; redeploy automation |
| 403 al aprobar | Gobernanza funcionando | Usa el rol que indica `required_approver_role` |
| 400 `No action plan` | Plan sin pasos ejecutables | Revisa la respuesta del plan en VICTOR |
| Aprobado pero `FALLIDO` | VICTOR no pudo ejecutar en la EC2 | `docker compose logs executor`; confirma el mount `/opt/xoc-test` y que el archivo existe |
| VICTOR no arranca | Falta `ANTHROPIC_API_KEY` o build fallo | Revisa `/var/log/xoc-test-bootstrap.log` y `/opt/victor/.env` |

## Limpieza

- Borrar tickets: `DELETE /tickets/{id}` con el admin del tenant.
- Borrar el tenant: panel superadmin de la plataforma.
- Borrar la EC2: eliminar el stack de CloudFormation (borra instancia, EIP y SG).
