# F2 — Capa normalizada de precio (Darwinex XTIUSD M1 → UTC)

**Estado:** G0 aprobado por el usuario (2026-10-16, ADR-019). Primer bloque del resto de F2.

## Objetivo
Pasar las barras M1 de Darwinex (hora servidor) a una tabla normalizada en UTC, validada, determinista y trazable al fichero raw. Sin rellenar barras ausentes ni calcular nada de etiquetas (eso es F6).

## Entradas
- `data/raw/darwinex/extracted_v2/XTIUSD_darwinex_M1 - copia.csv`.
  - Extraído del RAR `darwinex/xti_m1_sqx_20261016_v2.bin`, que está en `manifests/darwinex.jsonl`.
  - Su sha256 (`ab0ede34…68aa`) se fija en `config/darwinex.yaml` y se registra en `manifests/darwinex_extract.jsonl`.
  - Si no coincide → `RawIntegrityError`.
- `config/darwinex.yaml`:
  - desfase hora servidor = NY + 7 h (inferido en F1, `reports/f1/darwinex.md` §3);
  - pausa diaria 17:00–18:00 ET;
  - el sello de la barra es su **inicio**;
  - umbral de descarte.

## Salidas
- `data/normalized/darwinex_xti_m1/<version>/YYYY.parquet`: un fichero por año de `trade_date`, escrito una vez y en solo lectura.
- `manifests/normalized/darwinex_xti_m1/<version>.json` con:
  - sha256 de la entrada y de cada salida;
  - commit;
  - filas leídas, conservadas y descartadas (por motivo);
  - rango temporal.

### Esquema de salida (pandera)
| Columna | Tipo | Significado |
|---|---|---|
| `bar_start_utc` | Datetime UTC | Inicio del minuto |
| `bar_end_utc` | Datetime UTC | Inicio + 1 min |
| `available_at` | Datetime UTC | = `bar_end_utc`: el cierre de la barra no se conoce antes |
| `trade_date` | Date | Día de negociación: hora NY ≥ 18:00 pertenece al día siguiente |
| `bid_open`, `bid_high`, `bid_low`, `bid_close` | Float64, USD/bbl | OHLC sobre el bid (declarado por el usuario) |
| `tick_volume` | Int64 | Nº de ticks del minuto (> 0) |
| `avg_spread` | Float64, USD/bbl | Spread medio ask − bid del minuto (≥ 0) |
| `source_line` | Int64 | Línea del CSV (trazabilidad) |

## Reglas y por qué
1. **Conversión:** hora NY = hora servidor − 7 h, y después NY → UTC.
   - Una hora NY inexistente o ambigua (cambios de horario) lanza `CalendarError`. El mercado está cerrado a esas horas; hoy hay 0 casos.
2. **Sello = inicio de barra.** Evidencia en F1:
   - la primera barra semanal es domingo 18:00 ET (apertura);
   - la última del viernes es 16:59 ET;
   - no hay barras 17:01–17:59.
3. **Pausa diaria:** las barras con hora NY en [17:00, 18:00) se descartan con motivo `daily_break`. Hoy hay 6, todas a las 17:00 en 2018.
   - Fuera de sesión (sábado; domingo antes de 18:00; viernes desde 17:00) → motivo `outside_week`.
   - Si el descarte supera `max_drop_fraction` → `SchemaError`.
4. **Validación dura (lanza `SchemaError`):**
   - duplicados;
   - desorden;
   - precio ≤ 0;
   - High < max(Open, Close) o Low > min(Open, Close);
   - spread < 0;
   - TickVolume ≤ 0;
   - formato de fecha u hora inválido.
5. **Sin rellenos:** los minutos sin barra siguen sin barra.
6. **Inmutable y determinista:**
   - salida ordenada por `bar_start_utc`;
   - si la versión ya existe, se verifica el hash y no se reescribe;
   - si difiere → `RawIntegrityError`.

## Invariantes críticos
- `available_at = bar_end_utc > bar_start_utc`; todo en UTC (`require_utc_column`).
- Ninguna barra en la pausa diaria ni en fin de semana.
- `trade_date` coherente: la hora NY de toda barra cae en [`trade_date` − 1 18:00, `trade_date` 17:00).

## Tests (antes de implementar)
- Conversión a mano:
  - invierno (EST): servidor 2018-01-08 01:00 → 2018-01-07 23:00 UTC;
  - verano (EDT): servidor 2018-07-09 01:00 → 2018-07-08 22:00 UTC.
- Semanas de cambio de horario (marzo/noviembre): domingo 18:00 ET convertido con el offset correcto.
- Hora NY inexistente o ambigua → `CalendarError`.
- `trade_date`: 17:59 → hoy; 18:00 → mañana; domingo 18:00 → lunes.
- Pausa y fin de semana descartados y contados; umbral superado → error.
- Cada validación dura con su caso.
- Integridad: sha256 de entrada distinto → error.
- Salida de solo lectura más manifiesto; la segunda ejecución verifica y no reescribe; salida alterada → error; hash determinista.

## Riesgos
- El desfase NY + 7 se infirió de los datos, sin documentación del bróker (riesgo residual en F1 §3).
- La regla `trade_date` sigue a la pausa observada en Darwinex, no al calendario de CME.
- Festivos con sesión corta: se ven como minutos sin barra; no se tratan aquí (calendario en F4/F6).

## Hecho cuando
- `make check` en verde.
- Revisión adversarial resuelta.
- Ejecución sobre el fichero real con informe de conteos en `reports/f2/darwinex_normalized.md`.
