# Push Notifications — Fase 1

## Alcance

Esta fase permite registrar devices del usuario autenticado, desactivarlos y enviar una notificación de prueba al propio device. No hay campañas, audiencias, envío a todo un tenant, EventBridge, SQS, DLQ ni cambios en RDS.

La fuente de identidad es el JWT/contexto autenticado existente: `tenant_id`, `user_id` y `role`. Nunca se aceptan tenant ni usuario en el body.

## Recursos

- Stack: `xoc-api-ops-<stage>`.
- DynamoDB: `xoc-api-ops-<stage>-device-registry`.
- PK: `TENANT#<tenantId>`.
- SK: `USER#<userId>#DEVICE#<deviceId>`.
- Región: `us-east-1`.
- AWS End User Messaging Push Application: configurada externamente mediante `END_USER_MESSAGING_APPLICATION_ID`.

## Registro de device

`POST /devices` guarda el token, `tokenHash` SHA-256, plataforma, proveedor, estado y metadatos del dispositivo.

Android:

```json
{
  "deviceId": "android-test-001",
  "platform": "android",
  "pushProvider": "fcm",
  "pushToken": "<FCM_TOKEN>",
  "notificationsEnabled": true
}
```

iOS development/dev-client/ad-hoc:

```json
{
  "deviceId": "ios-test-001",
  "platform": "ios",
  "pushProvider": "apns",
  "pushToken": "<APNS_DEVICE_TOKEN>",
  "apnsEnvironment": "sandbox",
  "notificationsEnabled": true
}
```

iOS TestFlight/App Store:

```json
{
  "deviceId": "ios-test-001",
  "platform": "ios",
  "pushProvider": "apns",
  "pushToken": "<APNS_DEVICE_TOKEN>",
  "apnsEnvironment": "production",
  "notificationsEnabled": true
}
```

`apnsEnvironment` solo es válido para `platform=ios` y `pushProvider=apns`.

- Si no se manda, `dev`/`staging` usan `sandbox` y `prod` usa `production`.
- Un override explícito es válido: una build development puede registrar `sandbox` incluso contra el backend `prod`.
- En Android enviar `apnsEnvironment` se rechaza para detectar contratos erróneos temprano.

## Envío de prueba

`POST /notifications/test` busca el device por el tenant y usuario del JWT. Solo envía si `status=ACTIVE` y `notificationsEnabled=true`.

| Device | ChannelType | MessageConfiguration |
| --- | --- | --- |
| Android / FCM | `GCM` | `GCMMessage` |
| iOS / APNs sandbox | `APNS_SANDBOX` | `APNSMessage` |
| iOS / APNs production | `APNS` | `APNSMessage` |

El campo `ChannelType` para sandbox es `APNS_SANDBOX`; el nombre del mensaje sigue siendo `APNSMessage`.

Respuesta temporal de diagnóstico:

```json
{
  "status": "sent",
  "deliveryStatus": "SUCCESSFUL",
  "statusCode": 200,
  "statusMessage": "Accepted",
  "channelType": "APNS_SANDBOX",
  "platform": "ios",
  "pushProvider": "apns",
  "apnsEnvironment": "sandbox",
  "deviceId": "ios-test-001"
}
```

Un fallo puede devolver `PERMANENT_FAILURE` y un mensaje como `BadDeviceToken`, sin devolver el token ni su hash completo.

## Estados y DELETE

Los estados permitidos son `ACTIVE`, `INACTIVE` e `INVALID`.

- Un registro habilitado queda `ACTIVE`.
- `DELETE /devices/{deviceId}` hace soft delete: cambia a `INACTIVE`, desactiva notificaciones y registra `deactivatedAt`.
- Un device `INACTIVE` o `INVALID` no puede recibir `/notifications/test`.
- Errores de proveedor que indican token no utilizable, como `BadDeviceToken`, `DeviceTokenNotForTopic`, `Unregistered` o `NotRegistered`, conservan el item para auditoría y lo cambian a `INVALID` con `invalidatedAt`, razón y detalle de fallo.

Un fallo global de credenciales APNs, como `InvalidProviderToken` o `ExpiredProviderToken`, no invalida el device: debe corregirse la configuración del canal APNs. `BadCertificateEnvironment` se registra como problema de entorno y marca el device como inválido para evitar nuevos envíos con esa configuración.

## Logs seguros

CloudWatch recibe eventos `push_send_requested` y `push_send_result` con tenant, usuario, device, plataforma, proveedor, entorno APNs, tipo de canal, estado de delivery, status HTTP/proveedor, mensaje, request ID y hash parcial del token.

Nunca se registra:

- Push token completo.
- JWT, access token, refresh token o Authorization header.
- Firebase service account.
- APNs `.p8`, private keys o credenciales.

## Pruebas

1. Registrar Android y confirmar `ACTIVE`; enviar `/notifications/test` y validar `channelType=GCM`.
2. Instalar una build development iOS en hardware físico, registrar con `apnsEnvironment=sandbox` y validar `channelType=APNS_SANDBOX`.
3. Instalar una build TestFlight/App Store, registrar con `apnsEnvironment=production` y validar `channelType=APNS`.
4. Para un `PERMANENT_FAILURE`, revisar `statusMessage` y el evento seguro `push_send_result` de CloudWatch.
5. Desactivar mediante DELETE y confirmar `INACTIVE`; el endpoint de prueba no debe enviar a ese device.

Para probar iOS revisar también bundle ID, Team ID, Key ID, `.p8`, Push Notifications capability, provisioning profile con `aps-environment` y ambos entornos APNs habilitados en AWS.

No pegar JWT ni tokens de push en chats, tickets o prompts.
