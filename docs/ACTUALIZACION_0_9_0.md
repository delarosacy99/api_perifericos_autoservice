# Agente de autoservicio 0.9.0 — instalación y puesta en marcha

Base: agente 0.8.6. Esta actualización incorpora HTTP y WebSocket para la pantalla, integración con `/api/autoservice/`, entrega durable de billetes y control de trabajos impresos mediante **JSPrintManager**. Las reglas de venta, premios, sobrantes, caja e impuestos permanecen en la API central.

## Aplicar la actualización

1. Detener la tarea de Windows del agente y cerrar el frontend. Guardar copia del proyecto y de `var/` con el agente detenido. No borrar ni sustituir la base SQLite ni el diario del NV200.
2. Copiar los archivos de `api_perifericos_autoservice/` del paquete sobre la instalación 0.8.6, respetando sus rutas. Son archivos completos nuevos/modificados. No incluye su `.env`, credenciales, base local ni DLL nuevas.
3. Mantener el entorno Python existente y ejecutar `python -m pip install -r requirements.txt`. No hay dependencias nuevas para esta actualización.
4. Agregar a su `.env` las opciones siguientes. La URL y las credenciales de ejemplo deben sustituirse por las reales.

```dotenv
BRIDGE_ENABLED=true
CENTRAL_API_BASE_URL=https://api.example.com/api/autoservice/
CENTRAL_AGENT_CREDENTIAL=REEMPLAZAR_CON_CREDENCIAL_DE_LA_ESTACION
CENTRAL_TIMEOUT_SECONDS=8
BRIDGE_LEASE_SECONDS=45
BRIDGE_HEARTBEAT_SECONDS=5
BRIDGE_WORKER_INTERVAL_SECONDS=1
BRIDGE_PRINT_TIMEOUT_SECONDS=90
JSPM_PRINTER_NAME=CUSTOM K80
CORS_ALLOWED_ORIGINS=https://kiosk.example.com,http://localhost:5173
```

`LOCAL_API_KEY` debe conservar un valor largo y único. `DJANGO_ALLOWED_HOSTS` debe incluir solo los hosts de loopback que se usarán. La moneda `CASH_CURRENCY`, el dataset físico y la moneda de la estación central deben coincidir. No se aplica conversión monetaria.

En producción usar `DJANGO_DEBUG=false`, `DJANGO_SECRET_KEY` propia y HTTPS para la API central. El puente únicamente acepta HTTP central cuando DEBUG está habilitado y el destino es loopback, para pruebas locales. La URL debe terminar en `/api/autoservice/`; no usar una URL que redirija.

Con el NV200 real, conservar `CASH_ACCEPTOR_DRIVER=nv200_essp`, su puerto COM y `NV200_POLL_WITH_ACK=true`. El puente rechaza la configuración sin ACK de hardware. El resto de opciones del billetero, CRT-603 y XL128 se conserva.

Para el flujo nuevo no es necesario cargar el SDK de la K80: puede dejar `PRINTER_MONITOR_DRIVER=simulator`. El estado de ese simulador **no representa el estado de JSPrintManager ni de la impresora física**. Los monitores SDK antiguos permanecen como diagnósticos opcionales; no participan en la nueva impresión ni confirman entregas.

## Migración y arranque

Desde la carpeta del proyecto, con su entorno activado:

```powershell
python manage.py migrate --noinput
python manage.py check
python manage.py bridge_status --probe
.\start_autoservice.cmd
```

El comando `bridge_status --probe` verifica el bootstrap central sin mostrar tokens. No habilita el billetero ni crea una venta. Puede indicar recuperación pendiente.

`start_autoservice.cmd` aplica migraciones antes de cada arranque de Daphne y conserva el enlace `127.0.0.1:8765`. La tarea de Windows existente sigue usando ese script. Debe existir **un solo proceso** de Daphne: el bloqueo del sistema operativo rechaza otro agente sobre la misma instalación. No ejecutar con múltiples workers ni con autoreload. Para desarrollo, `python manage.py runserver 127.0.0.1:8765 --noreload`.

El worker del puente arranca dentro del proceso ASGI; no hay que instalar Redis, Celery ni otro servicio para él. WSGI y otros servidores que no carguen `config.asgi` no ejecutan este circuito. Al iniciar se invalida el control anterior de pantalla y se inhibe la recepción pendiente antes de atender otro cliente.

## Preparar JSPrintManager

Instalar el controlador Windows de la K80 y la aplicación cliente de JSPrintManager en la estación. El frontend debe cargar una versión compatible de su biblioteca JavaScript y configurar su autorización/licencia según la instalación. Este paquete no redistribuye el cliente comercial ni su licencia.

Configurar el nombre exacto de la impresora instalada en `JSPM_PRINTER_NAME`. La biblioteca del frontend enviará el documento con `JSPM.InstalledPrinter`; no se utilizará la impresora predeterminada de Windows de forma implícita.

El módulo `examples/frontend/autoservice-bridge.mjs` incluye el cliente HTTP/WebSocket y `printWithJSPrintManager`. El equipo del frontend debe conectar su renderer de ticket existente: recibe los datos centrales y devuelve PDF base64 o comandos de impresión. No incluye un nuevo diseño de ticket, una pantalla completa, un instalador Tauri ni la biblioteca comercial de JSPrintManager.

La guía `GUIA_FRONTEND_ESTACION_AUTOSERVICIO.md` documenta los endpoints y el flujo. Se debe confirmar JSPrintManager disponible antes de habilitar la recepción de efectivo.

## Estados de impresión

| Estado local | Acción |
|---|---|
| `ready` | Puede reclamarse el documento una vez. |
| `dispatching` | Tiene un `attempt_id`; esperar resultado, sin enviar otra copia. |
| `confirmed` | Existe confirmación de la cola o resolución técnica de entrega. El agente reintenta el ACK central si falta. |
| `uncertain` | No se sabe si salió papel. Mantener atención pendiente y pedir asistencia. |
| `failed` | Intento fallido conocido. Revisar/cancelar cualquier trabajo residual antes de autorizar reimpresión. |

La resolución de `sendToClient()` o un callback `onFinished` sin estado positivo no cierra la atención. El agente verifica nombre del trabajo, impresora y estado positivo `PRINTED`/`COMPLETE`, sin flags de error. La información de la cola de Windows no constituye una medición física del papel entregado: esa capacidad debe validarse con el controlador instalado. Un resultado incierto nunca se reimprime solo.

Para resolver un trabajo incierto/fallido, una herramienta de soporte autenticada con la clave local llama `POST /api/v1/bridge/print-jobs/{job_id}/resolve/`:

```json
{"decision":"confirm_printed","reason":"Técnico verificó que el comprobante fue entregado al cliente."}
```

O, después de cancelar cualquier envío residual y comprobar que no se entregó:

```json
{"decision":"retry","reason":"Se canceló la cola anterior y se comprobó que no se entregó papel."}
```

Un token de pantalla no puede ejecutar esa resolución. No poner ese control al alcance del cliente anónimo. La reimpresión conserva el mismo ticket financiero y crea un nuevo intento físico.

## Recuperación de billetes

Cada billete se guarda primero en SQLite con `synchronous=FULL`. La API central recibe como `event_id` el UUID persistente de `AcceptedBanknote`, no el identificador textual del puerto. Un timeout se reintenta con el mismo UUID e importe. La entrega central queda registrada en `CashDelivery`.

El archivo `var/nv200-ack.json` conserva el lote físico y sus identidades durante un reinicio. Un lote pendiente antes del ACK se recupera con los mismos identificadores. Una caída en la ventana ambigua del ACK deja `ack_intent`: el agente bloquea la aceptación y requiere conciliación técnica; no presupone que sea seguro repetir ni descartar el crédito.

No borrar `var/` para resolver ese estado. Detener el agente, contrastar billetes guardados, contadores y estado del equipo. Solo después de determinar si el ACK se aplicó, usar uno de estos comandos:

```powershell
python manage.py resolve_nv200_ack --decision ack-applied --reason "Evidencia técnica de que el dispositivo aplicó el ACK" --confirm-reconciled
python manage.py resolve_nv200_ack --decision ack-not-applied --reason "Evidencia técnica de que el dispositivo conserva el lote pendiente" --confirm-reconciled
```

Son alternativas; no ejecutar ambas. El comando verifica que los créditos estén respaldados en la base y guarda la resolución con el lote anterior en `var/nv200-ack-resolutions.jsonl`. Si no hay evidencia suficiente, mantener bloqueado y escalar al técnico.

Al detener la recepción se drenan los créditos y se espera un intervalo de quietud. Si el equipo sigue procesando un billete, el cierre queda pendiente y el poller no vuelve a habilitarlo. No se traspasa ningún billete tardío a otra atención.

## Comprobación en la estación

Probar con la API central de esta etapa, Windows 11, NV200 y K80: billetes reales, corte de red, reinicio con atención abierta, cambio de papel, impresora desconectada, caída de pantalla, callbacks de JSPrintManager y reimpresión asistida. Verificar el saldo y “Pagado” en el POS después de reutilizar un premio y después de cobrar un sobrante.

Las pruebas automatizadas de esta entrega usan simulador de periféricos y dobles de transporte central/JSPrintManager. También se ejecuta una regresión del módulo central por separado. No sustituyen la prueba del recorrido físico. La aceptación de concurrencia financiera de la API central en PostgreSQL continúa siendo una comprobación independiente.

Referencias del proveedor: [API ClientPrintJob](https://www.neodynamic.com/Products/Help/JSPrintManager9.0/apiref/classes/ClientPrintJob.ClientPrintJob-1.html), [seguimiento de trabajos](https://www.neodynamic.com/articles/How-to-trace-Print-Job-Status-from-Javascript/), [cliente JSPrintManager](https://www.neodynamic.com/downloads/jspm/).
