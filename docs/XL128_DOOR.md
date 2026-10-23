# Monitor de puerta frontal XL128 V1.2

## Alcance

Este módulo monitorea exclusivamente la puerta frontal. No abre, cierra ni acciona la cerradura.
La API HTTP expone únicamente una consulta `GET`; no existe un endpoint de escritura para este
dispositivo. Las puertas traseras quedan fuera de cobertura y no deben inferirse a partir de este
estado.

## Datos confirmados en el SDK KMY8215G

El documento `KMY8215G Door.doc` identifica el equipo como **XL128 V1.2** y especifica:

- alimentación del panel: DC 12 V, 3 A;
- comunicación: RS-232;
- formato serial: 9600 baudios, 8 bits, sin paridad, 1 bit de parada y sin control de flujo;
- comando de consulta: `1B 70`;
- respuestas enumeradas: `10`, `08`, `04`, `02` y `01`.

El SDK también enumera `1B 5F` y `1B 60` como comandos de apertura y cierre. Este proyecto no los
implementa ni los transmite porque la puerta será exclusivamente monitoreada.

El documento no asigna un significado abierto/cerrado a cada byte de respuesta. La prueba física
de esta estación confirmó repetidamente `00` con la puerta frontal cerrada y `01` con la puerta
frontal abierta. El driver conserva `raw_status_hex` y utiliza exclusivamente ese mapeo. Cualquier
otro byte se devuelve como `state=unknown`.

## Configuración

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

El puerto y los códigos corresponden a la estación probada. Un mismo código no puede aparecer en
ambos grupos. Si otra estación usa otro cableado, debe repetir la validación física antes de cambiar
el mapeo.

## Verificación física de solo lectura

Con la puerta frontal completamente cerrada:

```powershell
python manage.py probe_door --samples 10 --interval 0.5
```

El resultado debe ser `00`. Repite con la puerta frontal abierta y confirma `01`. Si los valores no
cambian, varían sin relación con la puerta o no hay respuesta, no asumas el estado: revisa el
cableado y el puerto antes de operar la estación.

## API

```http
GET /api/v1/devices/door/
X-Local-API-Key: valor-configurado
```

Respuesta calibrada:

```json
{
  "door_id": "front",
  "door_name": "Puerta frontal",
  "coverage": "front_door_only",
  "driver": "xl128_serial",
  "model": "XL128 V1.2",
  "connected": true,
  "monitoring_only": true,
  "state": "closed",
  "is_open": false,
  "raw_status_hex": "00",
  "sdk_documented_status": false,
  "physically_calibrated_status": true,
  "port": "COM1",
  "baud_rate": 9600,
  "observed_at": "2026-10-22T00:00:00Z"
}
```

`sdk_documented_status=false` para `00` solo indica que ese byte no aparece en la tabla del SDK; el
mapeo sí fue confirmado físicamente y `physically_calibrated_status=true`. Para cualquier byte no
mapeado, `state` es `unknown`, `is_open` es `null` y `physically_calibrated_status=false`. La falta
de respuesta o la imposibilidad de abrir el COM devuelve `503`.

## Monitoreo continuo y eventos

Cuando `DOOR_MONITOR_BACKGROUND_ENABLED=true`, el proceso ASGI consulta el XL128 cada 0.5 segundos.
El servicio debe ejecutarse con un solo proceso, porque un puerto COM solamente puede tener un
propietario. Dos lecturas consecutivas iguales confirman una transición y evitan eventos por rebote.

Los eventos se escriben transaccionalmente en el outbox local:

| Evento | Motivo |
| --- | --- |
| `door.front.snapshot` | Primer estado estable observado. |
| `door.front.opened` | Transición confirmada de cerrada a abierta. |
| `door.front.closed` | Transición confirmada de abierta a cerrada. |
| `door.front.disconnected` | Tres fallos de comunicación consecutivos. |
| `door.front.recovered` | Dos respuestas válidas después de una desconexión. |

Para ver los eventos recientes:

```powershell
python manage.py show_door_events --limit 20
```

El comando solo consulta SQLite; no abre `COM1`. Mientras el servidor esté activo no debes ejecutar
`probe_door`, porque ese comando intenta abrir el mismo puerto desde un segundo proceso. Para una
lectura puntual usa el endpoint HTTP o detén primero el servidor.

El modelo `DoorMonitorState` conserva el último estado confirmado y la condición de conexión entre
reinicios. Los eventos permanecen pendientes en el outbox hasta que se implemente la sincronización
con el futuro servidor remoto.
