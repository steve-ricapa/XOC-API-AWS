# Postman

Import `postman/XOC-API-AWS.postman_collection.json` into Postman.

Set at least these collection variables before running requests:

- `baseUrl`: API Gateway base URL, for example `https://2t7p2tu80l.execute-api.us-east-1.amazonaws.com`
- `adminEmail`
- `adminPassword`
- `companyName`
- `runtimeBaseUrl`: Azure Function base URL used by chat/SOPHIA

Recommended order:

1. `GET /health`
2. `POST /api/onboarding/tenant` or `POST /api/auth/login`
3. `PUT /api/companies/{companyId}/runtime-settings`
4. `POST /api/chat`

The collection auto-saves these variables when available:

- `accessToken`
- `refreshToken`
- `companyId`
- `userId`
- `sessionId`
- `chatThreadId`
