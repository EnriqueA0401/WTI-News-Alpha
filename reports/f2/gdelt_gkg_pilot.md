# F2 — Piloto GDELT GKG 2024–2025 (D5, filtro `gkg-oil-v2`)

Fecha: 2026-10-15.
- Ejecución: `wna ingest gdelt-gkg --start 2024-01-01 --end 2026-01-01`.
  - Commit `3f98ac3`, 4 procesos.
  - 65 min (21:04 → 22:09 UTC).
- Cifras sacadas de los manifiestos `manifests/gdelt_gkg_oil/gkg-oil-v2/*.jsonl.gz` con `scripts/f2/gdelt_gkg_pilot_report.py` y `scripts/f2/gdelt_gkg_lastmodified.py`.
- Detalle mensual: `gdelt_gkg_pilot_months.csv`. Retraso por fichero: `gdelt_gkg_lastmodified_lag.csv`.

## 1. Resultado
| Medida | Valor |
|---|---|
| Días procesados / fallidos | 731 / 0 |
| Ficheros del índice / descargados y verificados (md5 + tamaño) | 68.493 / 68.493 |
| 404 / reintentos | 0 / 0 |
| Filas GKG leídas | 92.489.629 |
| Filas conservadas | 2.869.166 (3,1 %; entre 2,8 % y 3,4 % según el mes) |
| Coincidencias por motivo (no excluyentes) | tema 2.766.411 · título 412.046 · URL 306.574 |
| Líneas rechazadas (registro mal formado o partido) | 212 (0,0002 %); 7 parecían relevantes |
| Caracteres UTF-8 inválidos sustituidos | 2.307 |
| Ficheros sin `Last-Modified` | 0 |
| `DATE` distinto del nombre del fichero | 0 |
| Tamaño en disco (Parquet zstd, con GCAM) | 9,0 GB, ≈ 4,5 GB/año |

## 2. Huecos de la fuente
- 2025-06: 16 días sin ningún fichero, y 2025-07 un día más. Es el hueco de 17 días ya visto en F1 (desde 2025-06-14 18:00 UTC).
- Esos días quedan registrados con 0 ficheros. Cuentan como hueco de la fuente, no como "sin noticias".
- Ningún fichero listado en el índice dio 404 en 2024–2025.

## 3. Hora real de publicación de los ficheros (evidencia para D6 / ADR-012)
`Last-Modified` menos el timestamp nominal, en los 68.493 ficheros:

| Medida | Valor |
|---|---|
| Mínimo / p1 / mediana / p99 / p99,9 / máximo | −13,0 / −12,0 / −10,5 / −7,9 / −6,6 / **+35,7 min** |
| Ficheros publicados después del nombre | 8 |
| … más de +5 min | 2 |
| … más de +15 min | 2 (+35,7 min el 2024-09-22 04:15; +20,2 min el 2025-05-04 21:15) |

- Ninguno de los 8 casos tardíos viene después de un hueco.
- **Conclusión:** la regla aprobada en A9 (nominal + 15 min) habría adelantado la disponibilidad en 2 ficheros de 68.493. La regla propuesta en D6, `max(nominal + 15 min, Last-Modified + 5 min)`, los cubre todos.

## 4. Implicaciones
- Volumen: ≈ 1,4 M artículos/año filtrados. Es un filtro amplio; la relevancia fina se decide en F3.
- Disco: con GCAM, el histórico 2015–2023 (más volumen en 2015–2019) ocuparía unos 40–60 GB (estimación sin medir). No cabe en el disco libre de la sesión (≈ 20 GB).
  - Opciones: quitar GCAM en las versiones siguientes (≈ 70 % menos) o un almacenamiento externo.
  - Decisión del usuario cuando se plantee ampliar.
- Riesgo aceptado: no se guardan las 212 líneas rechazadas (ADR-013).
