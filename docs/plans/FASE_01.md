# F1 — Auditoría de fuentes (puerta G0)

**Objetivo:** medir cobertura, calidad de timestamps, coste y licencia de cada fuente para decidir G0: intradía viable / diario-prospectivo / parar.

**Requisitos previos:** protocolo aprobado; `EIA_API_KEY`, `FRED_API_KEY`, `DATABENTO_API_KEY`; decisión ADR-005 y ADR-006.

**Límites:** muestras pequeñas (< 2 GB en total); ninguna descarga de pago sin aprobación; respetar rate limits y términos.

| Fuente | Qué se mide | Salida |
|---|---|---|
| CL / Databento | Datasets/schemas/rango GLBX.MDP3 (metadatos gratis); definiciones de instrumento (tick, vencimientos); **quote** `ohlcv-1m` (1 mes y rango completo) → **parada con coste exacto**; licencia; volumen/OI alrededor de vencimientos para fijar N del roll. | Sección CL + ADR de roll |
| GDELT 2.0 | Muestra estratificada (1 día/mes, 2015–2026) de Export/Mentions/GKG: artículos relevantes WTI/año, validez de timestamps, `DATEADDED` vs fichero, duplicados, acceso a títulos (DOC API con backoff). | Sección GDELT + retraso conservador (A9) |
| EIA | Series WPSR vía API; calendario con excepciones (web oficial); disponibilidad histórica de fechas/horas de publicación. | Calendario de releases verificado |
| FRED/ALFRED | Vintages de series candidatas; `DCOILWTICO` (Plan B diario, spot Cushing ≠ futuro CL). | Sección macro |
| CFTC COT | Disaggregated CL (código 067651); calendario y excepciones (cierres de gobierno). | Calendario COT |
| NOAA | Cobertura y existencia de hora de publicación. | Sección NOAA |

**Tests:** manifiestos (sha256, request, fecha); idempotencia (no re-descarga con checksum válido); parsers con fixtures reales pequeñas.

**Ejecución:** auditorías en paralelo con subagentes; revisión adversarial del informe.

**Hecho cuando:** `reports/F1_source_audit.md` con cifras medidas e incertidumbre, manifiestos, ADRs y recomendación G0 entregada al usuario.
