# F2 — FRED/ALFRED point-in-time (`fred-alfred-v2`)

Fecha: 2026-10-16. Plan: `docs/plans/FASE_02_fundamentales.md` §C. Reglas: ADR-014 (D9, D10), ADR-024 y ADR-025. Comando: `wna normalize fred`.

## Resultado
- **29.077 filas** (observación × vintage) de 5 series diarias, con su historial completo de revisiones desde 2015.
- **Consulta point-in-time:** `available_at ≤ t < superseded_at`.

| Serie | Filas | Observaciones | Nulos (".") | Retrasadas por calendario oficial | Vintage antes de la observación |
|---|---|---|---|---|---|
| DGS10 | 3.340 | 3.337 | 140 | 3 | 0 |
| DGS2 | 3.338 | 3.337 | 140 | 3 | 0 |
| T10YIE | 3.339 | 3.337 | 139 | 1.820 | 0 |
| VIXCLS | 3.343 | 3.337 | 90 | 32 | 32 |
| DTWEXBGS | 15.717 | 3.333 | 152 | 0 | 0 |

- **Regla de disponibilidad:** la más tardía entre:
  - 00:00 ET del día siguiente a la vintage (R-PIT 4);
  - la hora oficial: H.15 al día hábil siguiente a las 16:15 ET; H.10 el lunes siguiente a las 16:15 ET.
- **Hallazgos de la revisión:**
  - T10YIE tiene vintages del mismo día de la observación (desde 2019), así que pasa a la hora de H.15.
  - VIXCLS tiene 32 vintages fechadas antes de la propia observación (festivos).

## Limitaciones
- Las horas de la Fed salen de sus páginas vigentes; no están verificadas para todo el periodo.
- Una revisión fechada un día antes de su publicación real no se puede detectar con ALFRED.
- DTWEXBGS solo tiene historia point-in-time desde 2019-02-04 (D10).
