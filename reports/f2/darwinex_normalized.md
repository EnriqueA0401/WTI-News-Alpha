# F2 — Precio normalizado Darwinex XTIUSD M1 (`dwx-xti-m1-v3`)

> v1, v2 y v3 tienen salidas idénticas (mismos sha256). La v2 añade las validaciones de la revisión adversarial y la v3 el hash del código en el manifiesto.

Fecha: 2026-10-16. Plan: `docs/plans/FASE_02_precio.md`. Comando: `wna normalize darwinex`.

## Resultado
- **Entrada:** `darwinex/extracted_v2/XTIUSD_darwinex_M1 - copia.csv`, con sha256 `ab0ede34…68aa`.
  - La procedencia del RAR está en `manifests/darwinex_extract.jsonl`.
- **Salida:** `data/normalized/darwinex_xti_m1/dwx-xti-m1-v3/YYYY.parquet` (7 ficheros, 35 MB, solo lectura).
  - Manifiesto: `manifests/normalized/darwinex_xti_m1/dwx-xti-m1-v3.json`.
- **Filas:** 1.961.470 leídas, 1.961.464 conservadas.
- **Descartadas (6):**
  - `daily_break`: 4 barras a las 17:00 ET (2018);
  - `outside_week`: 2 barras a las 17:00 ET de un viernes (2018).
- **Re-ejecución:** verificada por sha256, sin reescribir.

| Año (`trade_date`) | Barras | Días | Primera barra (UTC) | Última barra (UTC) |
|---|---|---|---|---|
| 2017 (desde oct) | 70.202 | 64 | 2017-10-01 22:00 | 2017-12-29 19:59 |
| 2018 | 327.204 | 258 | 2018-01-01 23:00 | 2018-12-31 21:59 |
| 2019 | 330.581 | 258 | 2019-01-01 23:01 | 2019-12-31 21:59 |
| 2020 | 343.988 | 259 | 2020-01-01 23:00 | 2020-12-31 21:59 |
| 2024 | 348.868 | 256 | 2024-01-05 00:00 | 2024-12-31 21:59 |
| 2025 | 347.367 | 256 | 2025-01-01 23:00 | 2025-12-31 21:59 |
| 2026 (hasta 20-jul) | 193.254 | 142 | 2026-01-01 23:00 | 2026-07-20 23:59 |

## Limitaciones (heredadas de G0)
- Es el CFD del bróker, no el futuro CL. La conversión NY + 7 h está inferida de los datos (ADR-019).
- No hay datos de 2021–2023.
- Los minutos sin barra no se rellenan. Las sesiones cortas por festivo aparecen como minutos ausentes; el calendario de festivos se trata en F4/F6.
