# F2 — WPSR point-in-time (`eia-wpsr-v1`)

Fecha: 2026-10-16. Plan: `docs/plans/FASE_02_fundamentales.md` §A. Reglas: ADR-014 (D7–D9) y ADR-020. Comando: `wna normalize eia-wpsr`.

## Resultado
- **612 publicaciones** de 2015-01-07 a 2026-10-07 y **6.754 valores** (11 series semanales).
- **Hora de disponibilidad** (`time_status`):

| Estado | Publicaciones | Regla |
|---|---|---|
| `d8_standard` | 523 | Miércoles normal, 10:30 ET (D8, no verificado antes de dic-2024) |
| `rpit_date_verified` | 77 | Publicación desplazada (jueves/viernes/lunes): 18:00 ET de ese día (D9) |
| `official_exception` | 11 | Hora oficial de la tabla de excepciones (dic-2024 → hoy) |
| `rpit_date_unverified` | 1 | 2020-01-03, que no está en FRED 212: 18:00 ET del lunes siguiente |

- **Validaciones superadas:**
  - las 41 páginas del archivo con "week ending";
  - las 11 excepciones oficiales ya publicadas;
  - toda publicación tiene ≥ 1 semana.
  - Solo 2022-06-29 y 2023-11-15 llevan 2 semanas (semanas sin publicación propia: disponibilidad conservadora).
- **Valores:** todos con `revision_risk = true` (D7). WCESTUS1 antes de 2016-10-07 lleva además `known_redefinition = true`.

## Limitaciones
- Las 10:30 ET de 2015–2024 no están verificadas (riesgo aceptado en D8).
- Los valores son los actuales (revisados), no los de primera publicación (D7).
- Para "laborable" se usa lunes a viernes, sin calendario federal verificado (opm.gov da 403). Solo afecta a 1 publicación.
