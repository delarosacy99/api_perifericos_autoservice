# Monitor de estado CUSTOM K80

## Alcance

Este módulo consulta la impresora térmica `CUSTOM K80` por USB mediante la `CePrnLib.dll` x64 del
fabricante incluida en el proyecto. Es exclusivamente de monitoreo: no implementa impresión,
avance de papel, corte, expulsión ni reinicio.

El SDK recomienda esperar al menos 500 ms entre dos consultas de estado o información. El comando
`probe_printer` aplica ese mínimo cuando se solicitan varias muestras.

## Requisitos en Windows 11

La cola de impresión debe existir y Python debe ser de 64 bits:

```powershell
Get-Printer -Name "CUSTOM K80"
python -c "import struct; print('Python', struct.calcsize('P') * 8, 'bits')"
Test-Path ".\vendor\custom_k80\x64\CePrnLib.dll"
```

La última comprobación debe devolver `True`. La ruta puede sustituirse mediante
`CUSTOM_K80_CEPRNLIB_DLL_PATH`, aunque normalmente debe dejarse vacía para usar la copia incluida.
`CeSMLm.dll` no es un requisito de esta configuración.

## Configuración

```dotenv
PRINTER_MONITOR_DRIVER=custom_k80_ceprintlib
CUSTOM_K80_PRINTER_NAME=CUSTOM K80
CUSTOM_K80_CEPRNLIB_DLL_PATH=
CUSTOM_K80_STATUS_MAPPING_VALIDATED=false
```

Deja `CUSTOM_K80_CEPRNLIB_DLL_PATH` vacío para usar
`vendor\custom_k80\x64\CePrnLib.dll`.

`CUSTOM_K80_STATUS_MAPPING_VALIDATED` debe permanecer en `false` hasta completar las pruebas
físicas. El driver usa la tabla específica del manual de comandos K80, código `77200000003300`,
en lugar de inferir las máscaras desde el ejemplo genérico de CeSmLm.

`CePrnLib` entrega el DWORD como `PPUURRNN`: papel, usuario, error recuperable y error no
recuperable. Las pruebas físicas del 22 de octubre de 2026 confirmaron `00000000` en condición
normal y `20000000`/`21000000` al abrir el mecanismo de papel. De acuerdo con el manual, esos dos
últimos valores corresponden al byte de papel (`20` o `21`), no a tapa ni cortador.

## Prueba inicial segura

Cierra cualquier utilidad del fabricante que esté consultando la impresora y ejecuta:

```powershell
python manage.py probe_printer --samples 3 --interval 0.5
```

La operación inicializa la capa USB, abre la impresora por el nombre de la cola y ejecuta únicamente
`CeGetSts`. El resultado declara explícitamente que quedan excluidas las acciones `print`,
`paper_feed`, `cut` y `reset`.

Primero toma una muestra con la impresora encendida, con papel y la tapa cerrada. Luego abre la tapa
y repite. No provoques intencionalmente un atasco ni un fallo del cortador.

## Contrato HTTP

```http
GET /api/v1/devices/printer/
X-Local-API-Key: valor-configurado-en-LOCAL_API_KEY
```

Campos principales:

| Campo | Significado |
|---|---|
| `state` | `ready`, `warning` o `error` |
| `alerts` | Identificadores de las alertas activas |
| `paper_out` | Falta de papel según cualquiera de las señales del SDK |
| `paper_near_end` | El sensor físico alcanzó el umbral de poco papel |
| `cover_open` | Señal de tapa abierta |
| `print_head_up` | Señal de cabezal levantado |
| `print_head_up_or_paper_jam` | Señal conjunta que el K80 no separa entre cabezal levantado y atasco |
| `paper_jam` | Señal individual; permanece `false` con `custom_k80_ceprintlib` |
| `cutter_error` | El cortador no completó su operación |
| `paper_remaining_cm` | `null` con el driver `custom_k80_ceprintlib` |
| `raw_status_hex` | DWORD de FULL STATUS sin modificar |
| `status_bytes` | Bytes de papel, usuario, error recuperable y no recuperable |
| `mapping_physically_validated` | Indica si las máscaras ya se confirmaron en esta K80 |

La falta de `CePrnLib.dll`, una cola con nombre distinto, una impresora desconectada, ocupada por
otra utilidad o un error USB devuelven `503` con un código técnico estable.

## Papel restante

El driver actual puede detectar `paper_near_end`, pero `CePrnLib.dll` no expone
`CePrnGetTotPaperRemainingUsb`. Por eso `paper_remaining_cm` permanece en `null` y, cuando se activa
la alerta, `paper_remaining_error` explica que la estimación no está disponible.

La biblioteca opcional `CeSMLm.dll` sí ofrece una estimación en centímetros después de
`paper_near_end`. No mide directamente el diámetro ni el porcentaje del rollo. Su cálculo depende
de que:

- la K80 tenga instalado el sensor físico de poco papel;
- se haya configurado previamente en el dispositivo la distancia estimada entre el umbral de poco
  papel y el fin virtual;
- el contador se consulte después de activarse `paper_near_end`.

La API no ejecuta `CePrnSetVirtualPaperEndUsb`, porque esa llamada modifica la configuración de la
impresora. Si en el futuro se instala `CeSMLm.dll`, se mantendrá como driver opcional y deberá
validarse por separado antes de habilitar esa estimación.

## Máscaras K80 observadas en el DWORD de CePrintLib

| Señal | Byte | Máscara del DWORD |
|---|---|---:|
| Sin papel | Papel | `0x01000000` |
| Poco papel | Papel | `0x04000000` |
| Ticket en salida | Papel | `0x20000000` |
| Cabezal levantado o atasco | Usuario | `0x00010000` |
| Tapa abierta | Usuario | `0x00020000` |
| Sobretemperatura | Error recuperable | `0x00000100` |
| Error de comando no reconocido | Error recuperable | `0x00002000` |
| Error del cortador | Error no recuperable | `0x00000001` |

Hasta validar físicamente la K80, conserva también `raw_status_hex` y `status_bytes` en cualquier
reporte de prueba.

