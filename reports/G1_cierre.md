# Puerta G1 — Ingesta y normalización (F2): informe

Fecha: 2026-10-16. Criterio de G1: completitud, timestamps válidos, duplicación y cobertura documentados.

## Capas publicadas
Todas son inmutables, con manifiesto (sha256 de entradas, salidas, parámetros y código) y con revisión adversarial resuelta.

| Capa | Versión | Contenido | Hora de disponibilidad | Informe |
|---|---|---|---|---|
| Precio Darwinex M1 | `dwx-xti-m1-v3` | 1,96 M barras; 6 descartadas (pausa diaria o fin de semana) | Cierre de cada barra (UTC) | `reports/f2/darwinex_normalized.md` |
| EIA WPSR | `eia-wpsr-v2` | 612 publicaciones, 11 series semanales | 521 a las 10:30 ET (D8), 12 oficiales, 79 R-PIT | `reports/f2/eia_wpsr_normalized.md` |
| CFTC COT | `cftc-cot-v2` | 614 semanas | 210 con hora real de la API; el resto por regla + R-PIT | `reports/f2/cftc_cot_normalized.md` |
| FRED/ALFRED | `fred-alfred-v2` | 5 series diarias con historial de revisiones | Vintage + calendario oficial (H.15/H.10) | `reports/f2/fred_alfred_normalized.md` |
| Noticias GDELT | `gdelt-news-v2` | 12,0 M noticias, 70 meses | Fichero + 15 min, o la hora real si llegó tarde | `reports/f2/gdelt_news_normalized.md` |

## Comprobaciones
- **Completitud:**
  - noticias, 0 días fallidos en el periodo;
  - precio, sin huecos salvo 2021–2023 (no hay datos);
  - fundamentales, todas las semanas 2015–2026.
- **Timestamps:**
  - todos tz-aware en UTC;
  - ninguna disponibilidad anterior a su publicación según la evidencia disponible;
  - cada regla está documentada en un ADR (ADR-019 a ADR-026).
- **Duplicación:**
  - sin claves duplicadas en ninguna capa;
  - la deduplicación de artículos es trabajo de F3.
- **Calidad:** `make check` en verde (494 tests, 99 % de cobertura de ramas).

## Problemas abiertos
1. **Captura en vivo poco frecuente.** GitHub ejecuta el cron cada 3–4 h en vez de cada 5 min (7 ejecuciones el 2026-10-16). Solo capta una parte de los ficheros GDELT y no sirve para medir retrasos de minutos.
2. **Precio 2021–2023 pendiente** (tu re-descarga en SQX).
3. **Limitaciones aceptadas:**
   - el precio es un CFD, no el futuro CL;
   - los valores EIA están revisados (D7);
   - 10:30 ET sin verificar en 2015–2024 (D8);
   - fechas de la puesta al día COT 2019 previstas, no observadas (ADR-022).

## Recomendación
**Aprobar G1** y pasar a F3 (noticias → eventos: relevancia, taxonomía, clustering online y muestra auditada).
