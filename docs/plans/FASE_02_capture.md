# F2 (parcial) — Captura prospectiva de disponibilidad (D12)

**Estado:** D12 aprobado por el usuario (2026-10-15). No depende de Darwinex.

**Objetivo:** registrar desde ya, cada 5 min, cuándo aparecen por primera vez los datos que usarán las features. Así se validan empíricamente las reglas de disponibilidad (GDELT +15 min, WPSR 10:30 ET, COT 15:30 ET) y queda un registro point-in-time real para el periodo de test.

## Fuentes (todas sin clave)
| Fuente | Qué se mira | Clave del ítem |
|---|---|---|
| GDELT | `lastupdate.txt` (3 ficheros: export, mentions, gkg) + `Last-Modified` de cada fichero (HEAD) | nombre del fichero |
| EIA WPSR | `ir.eia.gov/wpsr/table1.csv`: semana del dato (cabecera), `Last-Modified` y ventana de publicación de la política CloudFront de la redirección (`DateGreaterThan`) | semana + Last-Modified |
| CFTC COT | API Socrata `72hh-3qpy`, CL (067651): última `report_date` y `:created_at` | report_date |

## Salidas
- Rama huérfana `capture-data` (solo la escribe el bot):
  - `observations/YYYY-MM.jsonl`: una línea cuando aparece un ítem nuevo o cambia;
  - `state.json`: último valor visto por fuente y hora del último chequeo.
- Cada observación lleva:
  - `observed_at_utc`: momento de la petición;
  - `previous_check_utc`: chequeo anterior con éxito. La disponibilidad real cae en el intervalo `(previous_check_utc, observed_at_utc]`; es una cota, no un instante exacto;
  - versión de código y `run_id`.

## Invariantes
- Append-only: nunca se reescriben observaciones.
- Timestamps UTC tz-aware.
- Un fallo de una fuente se registra como observación de error y la ejecución termina con código ≠ 0 (visible en GitHub). Las demás fuentes se registran igual.
- Sin secretos: todo público.

## Riesgos
- El cron de GitHub se retrasa a menudo (5–30 min): la resolución real es el intervalo registrado.
- GitHub desactiva los workflows programados tras 60 días sin actividad en el repo.
- El repo es público: las observaciones son públicas (solo metadatos de fuentes públicas).

## Tests (antes de implementar)
- Parseo de `lastupdate.txt`, de la política CloudFront y de la cabecera de `table1.csv`.
- Detección de cambios contra el estado.
- Ejecución completa con transporte simulado: primera vez, sin cambios, ítem nuevo, error de una fuente.

## Hecho cuando
- `make check` en verde.
- Ejecución real local escribe observaciones válidas.
- Workflow commiteado.
- El usuario sabe cómo ver la rama y desactivarlo.
