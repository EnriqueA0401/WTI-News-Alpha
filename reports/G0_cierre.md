# Puerta G0 — Informe de cierre (2026-10-16)

## Decisiones del usuario ya tomadas
ADR-014 a ADR-018:
- precio Darwinex XTIUSD;
- sin compras a Databento;
- periodo de trabajo 2018–2020 + 2024–2026; 2021–2023 se añadirá cuando llegue;
- reglas de disponibilidad D6–D9.

## Evidencia
| Pieza | Estado | Informe |
|---|---|---|
| Precio intradía (Darwinex M1) | 1,96 M barras válidas. Hora servidor = NY + 7 h, inferida de los datos. Frente al futuro CL de la EIA: diferencia mediana de 0,06–0,11 USD y correlación de retornos de 0,98. Sin saltos de roll (interpolación C1→C2). | `reports/f1/darwinex.md` |
| Noticias (GDELT GKG filtrado) | Años completos: 2018, 2019, 2020, 2024, 2025 y 2026 (hasta 14-oct). 0 días fallidos tras corregir 2020-05-19. 16,3 M filas conservadas de 324 M. | `reports/f2/gdelt_gkg_pilot.md`, `reports/f2/gdelt_gkg_gkg-oil-v3_months.csv` |
| Fundamentales | EIA WPSR (API, riesgo de revisión, D7), CFTC COT y FRED/ALFRED: auditados en F1, se ingieren en F2. | `reports/F1_source_audit.md` |
| Captura en vivo | En marcha cada 5 min (GDELT, WPSR, COT). | ADR-016 |

## Limitaciones declaradas
1. El precio es un CFD del bróker, no el futuro NYMEX CL. Desde 2024-04-06 no hay contraste externo.
2. Faltan 2021–2023 de precio, así que el periodo es discontinuo y queda en ≈ 5,5 años.
3. Reglas de disponibilidad elegidas por el usuario:
   - GDELT +15 min: el piloto encontró 2 de 68.493 ficheros más tardíos.
   - EIA con valores revisados.
   - WPSR a las 10:30 ET no verificado históricamente.
4. 2018 no tiene títulos: la relevancia solo sale de temas y URL.

## Recomendación
**G0 = intradía viable (condicionado)** sobre el CFD de Darwinex en 2018–2020 + 2024–2026. Seguir con el resto de F2:
- capa normalizada de precio en UTC;
- ingesta de fundamentales;
- normalización de noticias con `first_available_at`.

Después, la puerta G1 (calidad y cobertura).
