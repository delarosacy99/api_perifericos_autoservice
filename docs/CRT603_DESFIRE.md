# CRT-603 y MIFARE DESFire EV3

## Alcance

El módulo `apps.card_reader` pertenece al agente local de periféricos. Detecta el lector, activa
tarjetas ISO14443-4 Type A y obtiene metadatos DESFire. No registra clientes, no administra saldos
y no decide si una operación financiera está autorizada.

El saldo y la relación cliente-tarjeta pertenecerán al futuro servidor remoto de membresía.

## Conexión validada

| Elemento | Valor |
|---|---|
| Lector | CREATOR CRT-603 |
| Puerto de la estación probada | COM4 |
| Velocidad observada | 19200 baudios |
| Formato serial | 8 bits, sin paridad, 1 bit de parada (`8N1`) |
| Tarjeta probada | NXP MIFARE DESFire EV3, 8 KB |
| NV200 | COM2, independiente del lector |

El puerto puede cambiar entre estaciones, por lo que se configura con `CRT603_COM_PORT`. La unidad
instalada respondió a 19200, que se mantiene configurable mediante `CRT603_BAUD_RATE`.

## Protocolo CRT-603

Cada trama utiliza:

```text
[LONGITUD][COMANDO][DATOS...][BCC]
```

- `LONGITUD` cuenta desde sí misma hasta el último byte de datos.
- `BCC` es el XOR de todos los bytes anteriores.
- Una respuesta positiva repite el byte del comando.
- Una respuesta negativa utiliza el complemento del comando (`comando XOR 0xFF`).

Comandos utilizados por esta versión:

| Comando | Operación |
|---|---|
| `0x03` | Leer firmware del lector |
| `0x70` | Seleccionar modo ISO14443 Type A |
| `0x20` | Buscar y seleccionar la tarjeta |
| `0x30` | Activar la tarjeta y obtener ATS |
| `0x31` | Enviar APDU a la tarjeta ISO14443-4 |

Ejemplos de tramas completas:

```text
Type A reset:          02 30 32
GetVersion por APDU:   07 31 90 60 00 00 00 C6
```

## Inspección DESFire

`POST /api/v1/devices/card-reader/inspect/` ejecuta esta secuencia:

1. Abre el puerto serial configurado.
2. Lee el firmware del CRT-603.
3. Selecciona Type A y busca una tarjeta mediante WUPA.
4. Activa ISO14443-4 y conserva el ATS.
5. Ejecuta `GetVersion` (`9060000000`) y sus tramas adicionales (`90AF000000`).
6. Ejecuta `GetApplicationIDs` (`906A000000`).
7. Ejecuta `GetFreeMemory` (`906E000000`).

Una tarjeta nueva devuelve `9100` sin identificadores de aplicación. Para una DESFire EV3 de 8 KB
completamente vacía, `GetFreeMemory` devuelve 8192 bytes.

Respuesta abreviada:

```json
{
  "card_type": "mifare_desfire_ev3",
  "uid": "UID_EN_HEXADECIMAL",
  "atr": "ATS_EN_HEXADECIMAL",
  "capacity_bytes": 8192,
  "free_memory_bytes": 8192,
  "application_ids": [],
  "credential_application_id": "D3E001",
  "credential_state": "blank"
}
```

Estados de credencial:

| Estado | Significado |
|---|---|
| `blank` | La tarjeta no contiene aplicaciones |
| `application_present` | Existe el AID reservado para la credencial DEMP |
| `foreign_or_unprovisioned` | Existen aplicaciones, pero no el AID esperado |

La presencia del AID por sí sola no demuestra autenticidad. Para eso se debe usar
`verify_card_credential` o el endpoint de verificación, que realizan autenticación mutua AES y
validan el contenido protegido mediante CMAC.

## Límites de la API HTTP

- No existe un endpoint para enviar APDU arbitrarias.
- No hay endpoints de escritura, formato, creación de aplicaciones ni cambio de claves.
- El UID se devuelve como dato de inventario; nunca debe usarse como autenticador.
- No se almacenan nombre, documento, saldo ni movimientos en la tarjeta.
- `CARD_CREDENTIAL_APPLICATION_ID=D3E001` reserva el identificador de la credencial.

## Aprovisionamiento de laboratorio

La versión 0.7.0 conserva un comando administrativo separado de la API HTTP. La escritura continúa
bloqueada por defecto y requiere simultáneamente una tarjeta vacía, el UID exacto, `--execute`,
`DJANGO_DEBUG=true`, `CARD_ALLOW_TEST_PROVISIONING=true` y una clave maestra AES no nula.

El diseño de prueba utiliza:

| Elemento | Valor |
|---|---|
| AID | `D3E001` |
| Tipo de claves | AES-128 |
| Clave 0 | Administración, diversificada por tarjeta |
| Clave 1 | Autenticación y lectura, diversificada por tarjeta |
| Archivo | Estándar, número 0, 17 bytes |
| Contenido | Versión 1 + UUID opaco de 16 bytes |
| Lectura | Requiere clave 1 y valida CMAC |
| Escritura/cambios | Requieren clave 0 |

La entrada de diversificación combina el identificador de sistema `DEMP`, el UID, el AID y el
número de clave. El algoritmo AES-128 de diversificación sigue NXP AN10922.

El modo sin `--execute` solo genera un plan:

```powershell
python manage.py provision_test_card `
    --confirm-uid 04C94E2A771890 `
    --credential-id UUID-GENERADO-PARA-LA-PRUEBA
```

`verify_card_credential` realiza autenticación mutua AES, lee el UUID y comprueba el CMAC sin
modificar la tarjeta.

## Verificación desde la API local

El frontend puede solicitar una autenticación física con:

```http
POST /api/v1/devices/card-reader/credential/verify/
X-Local-API-Key: valor-configurado-en-LOCAL_API_KEY
```

No requiere cuerpo. Cada solicitud activa la tarjeta, selecciona `D3E001`, deriva la clave 1 a
partir del UID, realiza autenticación mutua AES, lee el archivo 0 y verifica su CMAC.

```json
{
  "uid": "04C94E2A771890",
  "application_id": "D3E001",
  "credential_id": "f0457aa7-e552-4ce8-b32f-f669bcc40b37",
  "authenticated": true,
  "credential_version": 1
}
```

La respuesta no contiene saldo, nombre, teléfono ni otra información de membresía. El servidor
remoto utilizará `credential_id` como identificador opaco para resolver esos datos cuando exista el
módulo de fidelización.

| Situación | HTTP | Código |
|---|---:|---|
| Credencial válida | 200 | — |
| Tarjeta ausente, aplicación faltante o autenticación inválida | 409 | error del lector |
| Clave maestra local ausente o inválida | 503 | `card_reader_configuration_error` |
| `X-Local-API-Key` ausente o inválida | 403 | autenticación local |

Las claves de producción no deben guardarse en `.env`, Git ni en texto legible dentro de cada
estación. Antes de uso real todavía faltan el módulo remoto de membresía, revocación, rotación de
claves, auditoría y custodia mediante MIFARE SAM AV3, HSM o un servicio criptográfico remoto.
