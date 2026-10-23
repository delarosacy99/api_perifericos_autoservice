# api_perifericos_autoservice

Agente local para controlar y monitorear los periféricos de una estación de autoservicio en
Windows 11. La aplicación web puede ejecutarse desde Tauri, Electron o un navegador en modo
kiosco, mientras este servicio mantiene acceso exclusivo a los dispositivos locales.

## Principio de arquitectura

Esta API es exclusivamente un **agente de periféricos**. No contiene reglas de negocio ni funciona
como libro financiero.

### Responsabilidades del agente local

- Conectar, configurar y monitorear el NV200.
- Detectar e inspeccionar tarjetas ISO14443-4/DESFire mediante el CRT-603.
- Consultar el estado de la puerta frontal mediante el panel XL128, exclusivamente en modo lectura.
- Consultar el estado de la impresora CUSTOM K80 por USB, sin imprimir, alimentar ni cortar papel.
- Habilitar o inhibir canales según la configuración de la estación.
- Detectar `NOTE CREDIT` y registrar cada billete aceptado.
- Mantener el total físico aceptado durante una recolección.
- Evitar el procesamiento duplicado de eventos del dispositivo.
- Garantizar que una sola recolección controle el billetero.
- Conservar eventos pendientes para sincronización y recuperación.
- Detener el hardware por solicitud remota, desconexión o error.

### Responsabilidades exclusivas del servidor remoto

- Importe de la jugada.
- Creación, cancelación y estado del ticket.
- Aplicación del efectivo a una venta.
- Sobrepago y saldo a favor.
- Comprobantes canjeables.
- Premios y pagos de caja.
- Prevención de doble consumo o doble cobro.

Por diseño, esta API no utiliza ni devuelve campos como:

```text
requested_amount_minor
consumed_amount_minor
credit_balance_minor
prize_amount_minor
voucher_amount_minor
```

## Estado de la versión 0.8.6

- Django 6.0.5 + Django REST Framework + ASGI.
- SQLite local con WAL, `busy_timeout`, claves foráneas y transacciones.
- Autenticación local mediante `X-Local-API-Key`.
- Perfil configurable por estación, país, zona horaria y moneda.
- Simulador NV200 con USD 1, 2, 5, 10, 20, 50 y 100.
- Driver real `nv200_essp` para Windows 11 mediante puerto COM/IF17.
- Transporte serie eSSP `9600 8N2`, CRC-16, byte stuffing y reintentos.
- Negociación Diffie-Hellman y cifrado AES-128 según el SDK de ITL.
- Detección automática de protocolo, número de serie, firmware, dataset y canales.
- `POLL WITH ACK`: el ACK se envía después de guardar `NOTE CREDIT` en SQLite.
- Polling interno a 200 ms, serializado con las operaciones de inicio y parada.
- Recuperación automática del puerto COM y rehabilitación de la colección activa.
- Estado `cashbox_present` y alertas activas expuestos por la API del NV200.
- Parada automática con `device_error` si se retira el cashbox, sin repetir la misma alerta en
  cada ciclo de polling.
- Conservación en memoria del mismo identificador de crédito si falla `EVENT ACK` y el puerto
  se reconecta dentro del mismo proceso.
- Driver serial directo del CRT-603, sin depender de la DLL propietaria.
- Simulador del CRT-603 y lectura segura de versión, UID, aplicaciones y memoria libre DESFire.
- Inspección validada con las respuestas de una MIFARE DESFire EV3 de 8 KB.
- Prueba física superada de autenticación PICC con la clave de fábrica DES/2K3DES.
- Aprovisionamiento DESFire de laboratorio disponible solamente por comando administrativo,
  bloqueado por defecto y condicionado al UID exacto.
- Aplicación `D3E001` con dos claves AES diversificadas por tarjeta y un UUID opaco protegido.
- Verificación posterior mediante autenticación mutua AES, lectura autorizada y CMAC.
- Endpoint local de solo lectura `POST /api/v1/devices/card-reader/credential/verify/`.
- Driver del panel de puerta XL128 V1.2 por RS-232 `9600 8N1`.
- Consulta de puerta frontal de solo lectura `1B 70`, con mapeo físicamente confirmado:
  `00` cerrada y `01` abierta.
- Cobertura declarada como `front_door_only`; las puertas traseras no se reportan ni se infieren.
- Monitoreo continuo opcional de la puerta frontal con antirrebote y detección de desconexión.
- Eventos persistentes `door.front.snapshot`, `opened`, `closed`, `disconnected` y `recovered`.
- Endpoint `GET /api/v1/devices/door/`; no existen endpoints para abrir o cerrar la puerta.
- Driver de monitoreo USB para la CUSTOM K80 mediante la `CePrnLib.dll` x64 incluida.
- Lectura de FULL STATUS con papel agotado, poco papel, tapa/cabezal, atasco y cortador.
- Decodificación específica del FULL STATUS documentado para la K80, respetando el orden de bytes
  `papel / usuario / error recuperable / error no recuperable` entregado por `CePrnLib`.
- La señal conjunta de cabezal levantado o atasco se expone sin afirmar cuál de las dos condiciones
  ocurrió; `raw_status_hex` y los cuatro bytes permanecen disponibles para diagnóstico.
- `paper_near_end` disponible; la cantidad restante en centímetros no está expuesta por
  `CePrnLib.dll` y queda reservada para una futura instalación opcional de `CeSMLm.dll`.
- Endpoint de solo lectura `GET /api/v1/devices/printer/`; no expone impresión, avance ni corte.
- `CashCollectionSession` como sesión técnica de recolección.
- `AcceptedBanknote` como registro inmutable de cada `NOTE CREDIT`.
- Outbox transaccional para el futuro servidor remoto.
- Verificación de moneda del dataset antes de habilitar el NV200.
- Sin módulo de membresía, saldo en tarjeta ni pagos RFID; el aprovisionamiento existente es
  exclusivamente una herramienta de laboratorio fuera de la API HTTP.
- Instalador reproducible de Windows para crear o validar `.venv`, ejecutar migraciones y pruebas,
  y registrar el inicio automático de Daphne al entrar el usuario del kiosco.
- Supervisor de Daphne con reinicio local en 10 segundos, estado operativo y desinstalación segura.

## Instalación en Windows 11

Requiere Python 3.12.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py test --verbosity 2
python manage.py runserver 127.0.0.1:8765
```

Antes de utilizarlo fuera de desarrollo, cambia en `.env`:

```dotenv
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=una-clave-larga-y-aleatoria
LOCAL_API_KEY=otra-clave-larga-y-aleatoria
```

El servicio debe escuchar únicamente en `127.0.0.1`, nunca en `0.0.0.0`.

### Inicio automático en Windows

No copies `.venv` entre PCs ni entre rutas: los entornos virtuales contienen referencias absolutas
al Python con el que fueron creados. Configura primero `.env` y mantén
`CARD_ALLOW_TEST_PROVISIONING=false`. Después, desde PowerShell como administrador, ejecuta:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\install_autostart.ps1 -StartNow
```

El comando crea o valida `.venv`, instala dependencias, aplica migraciones, ejecuta las pruebas y
registra `DEMP Autoservice API` al iniciar sesión del usuario interactivo. El iniciador escucha solo
en `127.0.0.1`, escribe en `logs\daphne.log` y reinicia Daphne 10 segundos después de una caída.

Consulta el estado con:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\status_autostart.ps1
```

La guía completa, incluida la reparación segura de un `.venv` inválido y la desinstalación sin
borrar datos, está en `docs/WINDOWS_AUTOSTART.md`.

## Configuración USD inicial

```dotenv
STATION_ID=AUTOSERVICE-DEV-001
STATION_COUNTRY_CODE=DO
STATION_TIMEZONE=America/Santo_Domingo

CASH_ACCEPTOR_DRIVER=simulator
CASH_CURRENCY=USD
CASH_EXPECTED_DATASET_CURRENCY=USD
CASH_ACCEPT_ALL_DATASET_DENOMINATIONS=true
CASH_ACCEPTED_DENOMINATIONS_MINOR=100,200,500,1000,2000,5000,10000
```

Los valores físicos se expresan como enteros en la unidad mínima de la moneda:

- `100` = USD 1.00
- `2000` = USD 20.00
- `10000` = USD 100.00

La moneda y las denominaciones reales no se programan desde esta API: las define el dataset
instalado en el NV200. La API lee ese dataset y habilita solamente los canales permitidos. Para
DOP será necesario instalar primero el dataset DOP correspondiente con las herramientas de ITL.

## Activar el NV200 real

El conector USB tipo B del NV200 se usa para programación. Para operación eSSP normal se utiliza
la interfaz IF17, que Windows expone como un puerto COM.

1. Instala el controlador IF17 incluido en el SDK.
2. Conecta el NV200 y confirma su puerto en **Administrador de dispositivos**.
3. También puedes enumerar los puertos desde la terminal:

```powershell
python -m serial.tools.list_ports
```

4. Cambia estas variables en `.env`:

```dotenv
CASH_ACCEPTOR_DRIVER=nv200_essp
NV200_COM_PORT=COM3
NV200_BAUD_RATE=9600
NV200_SSP_ADDRESS=0
NV200_COMMAND_TIMEOUT_SECONDS=1.0
NV200_COMMAND_ATTEMPTS=3
NV200_MIN_PROTOCOL_VERSION=6
NV200_MAX_PROTOCOL_VERSION=20
NV200_POLL_WITH_ACK=true
NV200_POLL_INTERVAL_SECONDS=0.2
```

`NV200_FIXED_KEY=0x0123456701234567` coincide con la clave predeterminada del SDK. No la cambies
solamente en `.env`: una clave fija diferente debe programarse también en el dispositivo.

Al iniciar una recolección, el agente realiza esta secuencia:

1. Abre el COM en `9600, 8 bits, sin paridad, 2 stop bits`.
2. Ejecuta `SYNC` y negocia la clave eSSP.
3. Selecciona el protocolo más alto compatible (mínimo 6).
4. Lee `SETUP REQUEST`, serie, firmware y versión del dataset.
5. Verifica la moneda, construye la máscara `SET INHIBITS` y ejecuta `ENABLE`.
6. Inicia `POLL WITH ACK`; solo `NOTE CREDIT (0xEE)` aumenta el total físico.
7. Guarda el billete y después envía `EVENT ACK`.

En producción ejecuta una sola instancia/proceso del agente, porque el puerto COM tiene un único
propietario. Por ejemplo:

```powershell
daphne -b 127.0.0.1 -p 8765 config.asgi:application
```

La conexión física fue validada en Windows 11 con el NV200 serie `6857174`, dataset DOP `DOP04G12`.
Se confirmaron créditos reales de RD$1,000 y RD$2,000, ausencia de duplicación, recuperación del
IF17, recuperación después de un corte eléctrico y detección del cashbox retirado. Antes de habilitar
una estación al público todavía deben probarse las demás denominaciones y los rechazos seguros.

La recuperación automática cubre desconexiones del IF17 y reinicios eléctricos del NV200 mientras
el proceso Daphne continúa ejecutándose. La recuperación de una colección activa después de cerrar
o reiniciar el proceso completo de la API se mantiene deshabilitada hasta implementar persistencia
durable del lote eSSP pendiente.

## Activar el CRT-603 real

La estación probada expone el lector en `COM4` a `19200` baudios. El número de puerto puede variar
entre instalaciones. El driver implementa directamente el protocolo UART del SDK: longitud,
comando, datos y BCC XOR. No requiere registrar ni cargar la DLL del fabricante.

1. Cierra `CTEST603_V1.0_EN`; solo un proceso puede utilizar el puerto serial a la vez.
2. Coloca una tarjeta DESFire EV3 sobre el lector.
3. Cambia estas variables en `.env`:

```dotenv
CARD_READER_DRIVER=crt603_serial
CARD_CREDENTIAL_APPLICATION_ID=D3E001
CARD_ALLOW_TEST_PROVISIONING=false
CARD_PROVISIONING_MASTER_KEY_HEX=
CRT603_COM_PORT=COM4
CRT603_BAUD_RATE=19200
CRT603_COMMAND_TIMEOUT_SECONDS=1.0
CRT603_WRITE_TIMEOUT_SECONDS=0.5
CRT603_COMMAND_ATTEMPTS=2
```

4. Ejecuta una inspección directa, sin iniciar el servidor:

```powershell
python manage.py inspect_card
```

Antes de aprovisionar una tarjeta de prueba, valida de forma no destructiva la configuración de
seguridad PICC y la autenticación con la clave maestra de fábrica:

```powershell
python manage.py probe_card_security
```

Este comando no crea aplicaciones, no cambia claves y no escribe archivos en la tarjeta.

### Aprovisionamiento de laboratorio

`provision_test_card` es una herramienta administrativa, no un endpoint HTTP. Sin `--execute`
solamente inspecciona la tarjeta y muestra el plan; nunca escribe. Incluso con `--execute`, exige:

- `DJANGO_DEBUG=true`;
- `CARD_ALLOW_TEST_PROVISIONING=true`;
- una clave maestra AES aleatoria de 16 bytes en `CARD_PROVISIONING_MASTER_KEY_HEX`;
- el UID físico exacto mediante `--confirm-uid`;
- una tarjeta DESFire EV3 completamente vacía;
- un UUID opaco mediante `--credential-id`.

El flujo crea `D3E001`, un archivo estándar de 17 bytes y dos claves AES distintas: administración
y autenticación/lectura. Ambas se diversifican con UID, AID, número de clave e identificador del
sistema. La tarjeta guarda solamente la versión del formato y el UUID; no guarda saldo ni datos
personales. Al terminar, el mismo comando verifica autenticación AES, contenido y CMAC.

Primero ejecuta únicamente el ensayo sin escritura:

```powershell
python manage.py provision_test_card `
    --confirm-uid 04C94E2A771890 `
    --credential-id UUID-GENERADO-PARA-LA-PRUEBA
```

No habilites `CARD_ALLOW_TEST_PROVISIONING` ni uses `--execute` hasta revisar ese resultado. La
clave maestra de laboratorio no debe pegarse en chats, incluirse en Git ni reutilizarse en
producción. Las estaciones de producción deberán usar un SAM/HSM o un servicio de claves.

Después de aprovisionar, la comprobación de lectura es no destructiva:

```powershell
python manage.py verify_card_credential
```

La misma verificación está disponible para el frontend mediante la API local. Este endpoint no
recibe saldo, cliente ni datos de pago y nunca escribe en la tarjeta:

```powershell
$baseUrl = "http://127.0.0.1:8765/api/v1"
$headers = @{ "X-Local-API-Key" = "EL_VALOR_REAL_DE_LOCAL_API_KEY" }

Invoke-RestMethod `
    -Uri "$baseUrl/devices/card-reader/credential/verify/" `
    -Method Post `
    -Headers $headers |
    ConvertTo-Json -Depth 10
```

Respuesta autenticada:

```json
{
  "uid": "04C94E2A771890",
  "application_id": "D3E001",
  "credential_id": "f0457aa7-e552-4ce8-b32f-f669bcc40b37",
  "authenticated": true,
  "credential_version": 1
}
```

Una tarjeta ausente, sin aprovisionar o con autenticación inválida devuelve `409`. Una estación sin
clave maestra configurada devuelve `503`. La API nunca devuelve la clave maestra.

También puede probarse mediante la API:

```powershell
$baseUrl = "http://127.0.0.1:8765/api/v1"
$headers = @{ "X-Local-API-Key" = "EL_VALOR_REAL_DE_LOCAL_API_KEY" }

Invoke-RestMethod `
    -Uri "$baseUrl/devices/card-reader/inspect/" `
    -Method Post `
    -Headers $headers |
    ConvertTo-Json -Depth 10
```

La inspección ejecuta solamente comandos de lectura: activa Type A, obtiene `GetVersion`, consulta
los identificadores de aplicaciones y lee la memoria disponible. No crea aplicaciones, no cambia
claves y no escribe datos. El contrato completo está documentado en `docs/CRT603_DESFIRE.md`.

## Activar el monitor de puerta XL128

El SDK confirma el modelo XL128 V1.2, RS-232 a `9600 8N1`, sin control de flujo. El driver solo
envía la consulta `1B 70`; los comandos documentados para abrir y cerrar no están implementados.

1. Confirma el puerto COM del panel en **Administrador de dispositivos** o con:

```powershell
python -m serial.tools.list_ports
```

2. Configura `.env` con el puerto y el mapeo confirmados en esta estación:

```dotenv
DOOR_MONITOR_DRIVER=xl128_serial
XL128_COM_PORT=COM1
XL128_BAUD_RATE=9600
XL128_COMMAND_TIMEOUT_SECONDS=1.0
XL128_WRITE_TIMEOUT_SECONDS=0.5
XL128_COMMAND_ATTEMPTS=2
XL128_DOOR_OPEN_RESPONSE_CODES=01
XL128_DOOR_CLOSED_RESPONSE_CODES=00
DOOR_MONITOR_BACKGROUND_ENABLED=true
DOOR_MONITOR_POLL_INTERVAL_SECONDS=0.5
DOOR_MONITOR_DEBOUNCE_SAMPLES=2
DOOR_MONITOR_OFFLINE_AFTER_FAILURES=3
DOOR_MONITOR_RECOVERY_SAMPLES=2
```

3. Con la puerta frontal cerrada, ejecuta una verificación:

```powershell
python manage.py probe_door --samples 10 --interval 0.5
```

4. Repite con la puerta frontal abierta. Debes obtener `00` de forma estable cuando está cerrada y
`01` cuando está abierta. Esta asignación fue confirmada físicamente; el SDK no documenta el
significado de `00`, por lo que `sdk_documented_status=false` con la puerta cerrada es informativo
y no representa un error.

5. Reinicia el servidor y consulta:

```powershell
Invoke-RestMethod `
    -Uri "$baseUrl/devices/door/" `
    -Method Get `
    -Headers $headers |
    ConvertTo-Json -Depth 10
```

La respuesta identifica `door_id=front` y `coverage=front_door_only`. La falta de respuesta
devuelve `503`. El contrato y el procedimiento completo están en `docs/XL128_DOOR.md`.

Con el monitoreo continuo habilitado, inicia el servidor ASGI con un solo proceso. Después de
abrir y cerrar la puerta puedes revisar los eventos persistidos sin acceder al puerto COM:

```powershell
python manage.py show_door_events --limit 20
```

El primer estado estable genera `door.front.snapshot`. Las transiciones siguientes generan
`door.front.opened` o `door.front.closed`. Tres consultas fallidas consecutivas generan
`door.front.disconnected`; dos respuestas válidas posteriores generan `door.front.recovered`.
Estos eventos son técnicos y no contienen reglas de negocio.

## Activar el monitor de impresora CUSTOM K80

La impresora instalada en la estación aparece como `CUSTOM K80` y está conectada por USB. El
driver predeterminado utiliza directamente la `CePrnLib.dll` x64 del fabricante incluida en el
proyecto. No depende de `CeSMLm.dll`, que no está instalada en la estación.

1. Comprueba la cola, la arquitectura de Python y la biblioteca incluida en PowerShell:

```powershell
Get-Printer -Name "CUSTOM K80"
python -c "import struct; print('Python', struct.calcsize('P') * 8, 'bits')"
Test-Path ".\vendor\custom_k80\x64\CePrnLib.dll"
```

Esta entrega requiere Python de 64 bits para el driver real de la K80.

2. Configura `.env`:

```dotenv
PRINTER_MONITOR_DRIVER=custom_k80_ceprintlib
CUSTOM_K80_PRINTER_NAME=CUSTOM K80
CUSTOM_K80_CEPRNLIB_DLL_PATH=
CUSTOM_K80_STATUS_MAPPING_VALIDATED=false
```

3. Ejecuta la consulta segura con la impresora encendida, papel colocado y tapa cerrada:

```powershell
python manage.py probe_printer --samples 3 --interval 0.5
```

4. Repite con la tapa abierta. No provoques intencionalmente un atasco ni un error del cortador.
   Conserva el JSON completo de ambas pruebas para confirmar las máscaras del modelo.

5. Cuando la prueba física esté confirmada, consulta la API:

```powershell
Invoke-RestMethod `
    -Uri "$baseUrl/devices/printer/" `
    -Method Get `
    -Headers $headers |
    ConvertTo-Json -Depth 10
```

Con este driver, `paper_near_end` sí se reporta pero `paper_remaining_cm` permanece en `null`:
`CePrnLib.dll` no expone la función de estimación en centímetros. Esa consulta solo se habilitará
si más adelante se instala y valida `CeSMLm.dll`. El procedimiento y el contrato completos están
en `docs/CUSTOM_K80_STATUS.md`.

## Flujo del simulador

Todas las solicitudes, salvo `health`, requieren:

```text
X-Local-API-Key: valor-configurado-en-LOCAL_API_KEY
```

### 1. Crear una recolección

`remote_session_id` es un identificador opaco generado por el futuro servidor remoto. El agente
solo lo utiliza para correlacionar eventos.

```http
POST /api/v1/cash/collections/
Content-Type: application/json

{
  "idempotency_key": "start-collection-0001",
  "remote_session_id": "remote-operation-0001",
  "currency": "USD"
}
```

### 2. Habilitar el billetero

```http
POST /api/v1/cash/collections/{collection_id}/start/
```

### 3. Simular un billete USD 20

Este endpoint solo está disponible con `CASH_ACCEPTOR_DRIVER=simulator`.

```http
POST /api/v1/devices/cash-acceptor/simulate-note/
Content-Type: application/json

{
  "collection_session_id": "{collection_id}",
  "denomination_minor": 2000
}
```

La respuesta actualiza únicamente el total físico aceptado:

```json
{
  "accepted_amount_minor": 2000,
  "currency": "USD",
  "status": "accepting"
}
```

### 4. Detener la recolección

```http
POST /api/v1/cash/collections/{collection_id}/stop/
Content-Type: application/json

{
  "reason": "remote_completed"
}
```

Razones admitidas:

- `remote_completed`
- `user_cancelled`
- `frontend_disconnected`
- `device_error`
- `service_shutdown`
- `operator_requested`

La razón describe por qué se detuvo el periférico. No genera saldos, tickets ni comprobantes.

## Endpoints actuales

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/v1/health/` | Salud mínima del servicio |
| GET | `/api/v1/station/configuration/` | Configuración efectiva de la estación |
| GET | `/api/v1/devices/cash-acceptor/` | Estado y canales del billetero |
| GET | `/api/v1/devices/card-reader/` | Estado local del CRT-603 |
| POST | `/api/v1/devices/card-reader/inspect/` | Inspección DESFire de solo lectura |
| POST | `/api/v1/devices/card-reader/credential/verify/` | Verificación AES de credencial |
| GET | `/api/v1/devices/door/` | Estado de la puerta frontal |
| GET | `/api/v1/devices/printer/` | Estado de la CUSTOM K80 |
| GET/POST | `/api/v1/cash/collections/` | Listar o crear recolecciones |
| GET | `/api/v1/cash/collections/{id}/` | Consultar una recolección |
| POST | `/api/v1/cash/collections/{id}/start/` | Habilitar el billetero |
| POST | `/api/v1/cash/collections/{id}/stop/` | Detener el billetero |
| POST | `/api/v1/devices/cash-acceptor/simulate-note/` | Inyectar `NOTE CREDIT` simulado |

## Pruebas

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --verbosity 2
```

La versión actual contiene 118 pruebas. Además de la cobertura del NV200, CRT-603 y XL128, incluye
la decodificación de FULL STATUS de la K80, carga de la biblioteca x64 incluida, inicialización por
nombre de cola, errores USB, endpoints autenticados y la garantía de que el monitor no expone
acciones de impresión, avance, corte ni reinicio.

## Próximo paso técnico

1. Completar la matriz física pendiente del NV200 con RD$50, RD$100, RD$200 y RD$500.
2. Validar `paper_near_end` cuando esté disponible un rollo próximo a agotarse.
3. Mantener `CUSTOM_K80_STATUS_MAPPING_VALIDATED=false` hasta cerrar esa matriz física.
