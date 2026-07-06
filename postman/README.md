# Postman

Import `postman/XOC-API-AWS.postman_collection.json`.

Variables mínimas:

- `baseUrl`: base URL del HTTP API, por ejemplo `https://xxxx.execute-api.us-east-1.amazonaws.com`
- `adminEmail`
- `adminPassword`
- `tenantName`
- `runtimeBaseUrl`

Contrato oficial: rutas limpias sin `/api`.

Orden recomendado:

1. `GET /health`
2. `POST /onboarding/tenant` o `POST /auth/login`
3. `PUT /tenant/runtime-settings`
4. `POST /chat`

Variables que la colección auto-actualiza:

- `accessToken`
- `refreshToken`
- `tenantId`
- `userId`
- `sessionId`
- `chatThreadId`
- `agentKeyId`
- `ticketId`
