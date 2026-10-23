# NV200 eSSP: guía de prueba física

Esta guía corresponde al driver `nv200_essp` del agente local. No modifica datasets ni contiene
reglas de venta, tickets, saldos o premios.

## Requisitos

- Windows 11.
- NV200 alimentado de acuerdo con el manual del fabricante.
- Interfaz IF17 y su controlador instalados.
- Puerto COM visible en Administrador de dispositivos.
- Dataset USD instalado para la primera prueba.
- Una sola instancia de la API en ejecución.

## Configuración mínima

```dotenv
CASH_ACCEPTOR_DRIVER=nv200_essp
CASH_CURRENCY=USD
CASH_EXPECTED_DATASET_CURRENCY=USD
CASH_ACCEPT_ALL_DATASET_DENOMINATIONS=true
NV200_COM_PORT=COM3
NV200_BAUD_RATE=9600
NV200_SSP_ADDRESS=0
NV200_POLL_WITH_ACK=true
```

## Comprobación sin aceptar billetes

1. Inicia la API.
2. Consulta `GET /api/v1/devices/cash-acceptor/` antes de una recolección; todavía puede aparecer
   desconectado porque la conexión se abre de forma diferida.
3. Crea una recolección y llama a `POST .../start/`.
4. Consulta de nuevo el estado y confirma:
   - `driver = nv200_essp`;
   - `model = NV200`;
   - `connected = true`;
   - número de serie real;
   - versión de firmware y dataset;
   - canales USD con los valores esperados.
5. Detén la recolección y confirma `accepting = false`.

Si la moneda reportada por el dataset no coincide con la configuración, el agente no ejecutará
`ENABLE`.

## Matriz mínima de prueba con efectivo

| Caso | Resultado esperado |
|---|---|
| Cada denominación USD | Un solo `AcceptedBanknote` por `NOTE CREDIT` |
| Billete inválido | Rechazo; no aumenta `accepted_amount_minor` |
| Billete de canal inhibido | Rechazo; no aumenta el total |
| Detener recolección | `DISABLE`, drenaje final de créditos y cierre local |
| Repetición antes de ACK | Mismo lote; no duplica el billete |
| Cashbox removido | `cashbox_present=false`, alerta activa, `DISABLE` y cierre con `device_error` |
| Atasco seguro/inseguro | Alerta `SAFE_NOTE_JAM` o `UNSAFE_NOTE_JAM` |
| Camino abierto | Alerta `NOTE_PATH_OPEN` |
| Puerto COM desconectado | `connected=false`; reintento automático y rehabilitación de la misma recolección al reconectar |

## Recuperación del IF17 y alimentación

La versión 0.4.0 recupera automáticamente una interrupción del COM mientras Daphne continúe
ejecutándose:

1. Un fallo de lectura, escritura o `EVENT ACK` cierra el transporte y publica
   `connected=false`, `accepting=false` en el estado local.
2. El poller conserva la recolección activa e intenta abrir nuevamente el mismo puerto COM.
3. Al volver el NV200, verifica de nuevo serie, dataset, moneda y canales.
4. Rehabilita las denominaciones configuradas y continúa el polling.
5. Si existía un crédito pendiente de `EVENT ACK`, reutiliza el mismo `device_event_id` para que
   SQLite no lo contabilice dos veces.

No introduzcas billetes mientras `connected=false` o `accepting=false`. Esta recuperación no cubre
todavía cerrar o reiniciar el proceso completo de Daphne con una recolección activa; ese caso se
probará después de persistir el lote eSSP pendiente en SQLite.

## Protección del cashbox

El estado de `GET /api/v1/devices/cash-acceptor/` incluye:

- `cashbox_present`: `true`, `false` o `null` cuando todavía no existe una lectura concluyente.
- `alerts`: contiene `cashbox_removed` mientras la última señal conocida sea de extracción.

Antes del primer `ENABLE` de una recolección nueva, el agente realiza un `POLL` con el validador
deshabilitado. Si el cashbox está retirado, responde con el error `cashbox_removed` y no habilita
ningún canal.

Si se retira durante una recolección, la primera transición `CASHBOX_REMOVED (0xE3)`:

1. cambia inmediatamente `accepting=false` y deshabilita los canales en el estado local;
2. registra una sola alerta aunque el NV200 repita la señal en cada `POLL`;
3. envía `DISABLE` y detiene la recolección con `stop_reason=device_error`.

Después de reinstalar el cashbox se debe crear una recolección nueva. El `POLL` previo al siguiente
`ENABLE` procesa `CASHBOX_REPLACED (0xE4)`, limpia la alerta y permite continuar.

## Validación física del 22 de octubre de 2026

- Equipo: NV200, serie `6857174`.
- Firmware: `NV02004651008000`.
- Dataset: `DOP04G12`.
- Canales detectados: RD$50, RD$100, RD$200, RD$500, RD$1,000 y RD$2,000.
- Créditos confirmados: RD$1,000 en canal 5 y RD$2,000 en canal 6.
- Total de prueba: RD$3,000, sin créditos duplicados.
- Recuperación confirmada después de desconectar/reconectar IF17 y después de cortar/restablecer
  la alimentación, sin reiniciar Daphne.
- `CASHBOX_REMOVED (0xE3)` confirmado físicamente. La protección automática incorporada en 0.8.4
  requiere una repetición final de esta prueba antes del despliegue público.

## Evidencia que se debe conservar

- Modelo y número de serie.
- Puerto COM usado.
- Firmware y dataset reportados.
- Lista de canales, moneda y valor.
- Log desde `SYNC` hasta el primer `NOTE CREDIT`.
- Resultado de cada caso de la matriz.
- Comportamiento después de desconectar y volver a conectar IF17.
- Comportamiento después de cortar y restablecer alimentación.

No se debe habilitar el equipo para público hasta completar esta matriz y revisar cualquier respuesta
eSSP desconocida que aparezca en el log.
