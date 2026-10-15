# Progreso

**Fase actual:** F1 terminada. **Puerta G0 pendiente de decisión del usuario** (`reports/F1_source_audit.md` §5; D5 aprobado, falta el resto). En curso: piloto GDELT GKG 2024–2025 (D5).

## Hecho
- F0 cerrada: esqueleto, núcleo (`timeutils`, `config`, `runlog`, CLI), protocolo aprobado (ADR-009). Revisión adversarial F0 resuelta (ADR-008).
- Especificaciones de CL verificadas con el Rulebook NYMEX cap. 200 (ADR-005).
- F1:
  - `ingestion/rawstore.py`: almacén raw inmutable con manifiesto sha256 e idempotencia (16 tests).
  - Auditorías con evidencia en `data/raw/` y `manifests/`: GDELT, Databento (solo metadatos y presupuestos, sin compra), EIA, FRED/ALFRED, CFTC y NOAA. Informes en `reports/f1/`.
  - Informe consolidado G0: `reports/F1_source_audit.md`.
  - Monitor en vivo de publicación de GDELT (`data/raw/gdelt_lag_monitor/`). Se cortó con un reinicio del contenedor (~05:05 UTC) y se relanzó a las 07:25 UTC.
- D5 (aprobado 2026-10-15): ingesta GDELT GKG por streaming (`ingestion/gdelt_gkg.py`, `wna ingest gdelt-gkg`, ADR-013).
  - Plan: `docs/plans/FASE_02_gdelt_gkg.md`.
  - Revisión adversarial: 14 hallazgos. Corregidos los 1–7 y 9–14; el 8 es un riesgo aceptado (bajo).
  - Prueba real de un día: 2024-06-10, filtro v1.
  - Piloto 2024-01-01 → 2026-01-01 con filtro v2, lanzado a las 21:04 UTC.
- `make check` en verde (206 tests, cobertura de ramas 99 %).

## Pendiente
- Decisiones G0 D1–D4 y D6–D12 (incluye compras Databento: A 2,80 USD; C 75,78 USD). D5 aprobado.
- Usuario: ¿Darwinex como fuente? Respondido: no sustituye al futuro CL; se recomienda mantener Databento (pendiente de confirmar).
- ADR-012 revisado (`max(nominal+15 min, Last-Modified+5 min)`): propuestas.
- Revisión adversarial G0 hecha: 11 hallazgos resueltos o convertidos en decisiones (`reports/F1_source_audit.md` §6).
- Credenciales: guardadas solo en la sesión (`~/.config/wna/secrets.env`, fuera del repo). Recomendado regenerarlas (se compartieron en el chat) y ponerlas en la configuración del entorno.

## Bloqueos
- Sin aprobación de G0 no se compra precio intradía ni se inicia F2.
- cmegroup.com, BLS y DOL inaccesibles (403) desde el entorno; GDELT DOC API con rate limit.
- La sesión llegó al límite de uso a las ~05:10 UTC; se reanudó a las 07:25 UTC.
