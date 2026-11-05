# Validación — agente 0.9.0

- 161 pruebas Python del agente: aprobadas (118 existentes y 43 nuevas).
- 5 pruebas Node del cliente JSPrintManager: aprobadas.
- API central, módulo autoservice: 73 pruebas ejecutadas; 66 aprobadas y 7 omitidas por requerir PostgreSQL.
- Django check y verificación de migraciones sin cambios pendientes: correctos.
- Migración SQLite nueva y actualización conservando efectivo previo: correctas.
- Proceso ASGI real con servidor central simulado: sincronización automática de billetes, venta, ACK de impresión y cierre correctos.
- Segundo proceso del agente rechazado mediante bloqueo del sistema operativo.
- Reinicio de proceso: misma atención, dinero conservado, billetero inhibido y sin duplicar crédito.

Las pruebas se ejecutaron en Linux/Python 3.12 con la versión de dependencias del agente. Los periféricos y JSPrintManager se simularon. Windows, NV200, K80 y el callback real del spooler deben probarse en la estación. No se certifica entrega física de papel a partir de estas pruebas.
