# F2 — COT point-in-time (`cftc-cot-v2`)

Fecha: 2026-10-16. Plan: `docs/plans/FASE_02_fundamentales.md` §B. Reglas: ADR-014 (D9), ADR-022 (decisiones del usuario) y ADR-023 (revisión adversarial). Comando: `wna normalize cftc-cot`.

## Resultado
- **614 semanas** (as-of 2015-01-06 → 2026-10-06) de Disaggregated Futures Only, NYMEX WTI 067651, con 14 columnas de posiciones.
- **Hora de disponibilidad** (`time_status`):

| Estado | Semanas | Regla |
|---|---|---|
| `api_created_at` | 210 | Hora real de la API (desde oct-2022), sin margen (ADR-022) |
| `rule_date` | 365 | 3.er día hábil federal tras el as-of, 18:00 ET (R-PIT 1) |
| `rule_date_unconfirmed_shift` | 25 | Festivo o cierre en medio, desplazamiento no confirmado: 18:00 ET del siguiente día hábil (R-PIT 2) |
| `catch_up_expected_rpit` | 11 | Puesta al día de 2019: fecha prevista + 1 día hábil, 18:00 ET (ADR-022/023) |
| `verified_release` | 3 | Publicación verificada por anuncio de la CFTC, 18:00 ET |

- **Calendario federal:**
  - festivos de 5 U.S.C. 6103, contrastados con el texto oficial;
  - 10 cierres de 9 órdenes ejecutivas (Federal Register).
- **Validaciones:**
  - la regla coincide con el calendario oficial 2026;
  - la API nunca es anterior a la regla ni a las 15:30 ET.
- Todas las filas llevan `revision_risk = true`: la CFTC no publica vintages.

## Limitaciones
- 2015–2022 depende de una regla validada, sin sellos de hora por fila.
- No se modelan los cierres de OPM por meteorología.
- Las fechas de la puesta al día de 2019 son las previstas, no las observadas (riesgo aceptado por el usuario).
