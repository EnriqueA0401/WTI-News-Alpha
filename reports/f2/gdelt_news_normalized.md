# F2 — Noticias normalizadas (`gdelt-news-v2`)

Fecha: 2026-10-16. Plan: `docs/plans/FASE_02_noticias.md`. Reglas: D6 (ADR-014) y ADR-026 (decisiones del usuario). Comando: `wna normalize news`.

## Resultado
- **12.002.831 noticias** en 70 ficheros mensuales (2,3 GB). Cuadra con los manifiestos raw: 2.869.166 (v2) + 9.133.665 (v3).
- **Disponibilidad:** `first_available_at` = max(hora del fichero + 15 min, `Last-Modified`).
  - 33 ficheros tardíos (2.875 noticias) usan su hora real; están marcados con `late_file`.
- **Exclusiones** (`exclude_reason`, 4.073 en total, el 0,03 %):
  - 3.476 por fecha de publicación más de 14 h por delante;
  - 597 por fecha absurda (antes de 1995).
  - 229.659 noticias con adelanto menor (huso horario) solo se marcan con `published_ahead`.

| Año | Noticias | En ficheros tardíos | Excluidas | Sin título |
|---|---|---|---|---|
| 2018 | 2.970.724 | 2.365 | 730 | todas |
| 2019 | 2.483.610 | 16 | 601 | 1.857.301 |
| 2020 | 1.733.891 | 478 | 1.276 | 0 |
| 2024 | 1.552.811 | 6 | 824 | 0 |
| 2025 | 1.316.355 | 10 | 599 | 0 |
| 2026 (hasta 14-oct) | 1.945.440 | 0 | 43 | 0 |

## Limitaciones
- La hora de publicación del artículo (`published_at`) no tiene huso verificado. Solo sirve para marcar incoherencias, nunca para adelantar la disponibilidad.
- 2018 y parte de 2019 no tienen título.
- Aún no hay deduplicación ni filtro de relevancia (F3). El filtro de F2 es amplio a propósito.
- `Last-Modified` es una aproximación a la hora de publicación (ADR-012).
