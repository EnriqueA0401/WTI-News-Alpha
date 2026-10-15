# Progreso

**Fase actual:** F1 — auditoría de fuentes terminada. **Puerta G0 pendiente de decisión del usuario** (`reports/F1_source_audit.md` §4, decisiones D1–D12).

## Hecho
- F0 cerrada: esqueleto, núcleo (`timeutils`, `config`, `runlog`, CLI), protocolo aprobado (ADR-009). Revisión adversarial F0 resuelta (ADR-008).
- Especificaciones de CL verificadas con el Rulebook NYMEX cap. 200 (ADR-005).
- F1:
  - `ingestion/rawstore.py`: almacén raw inmutable con manifiesto sha256 e idempotencia (16 tests).
  - Auditorías con evidencia en `data/raw/` y `manifests/`: GDELT, Databento (solo metadatos y presupuestos, sin compra), EIA, FRED/ALFRED, CFTC y NOAA. Informes en `reports/f1/`.
  - Informe consolidado G0: `reports/F1_source_audit.md`.
  - Monitor en vivo de publicación de GDELT (`data/raw/gdelt_lag_monitor/`). Se cortó con un reinicio del contenedor (~05:05 UTC) y se relanzó a las 07:25 UTC.
- `make check` en verde (139 tests, cobertura de ramas 99 %).

## Pendiente
- Decisiones G0 D1–D12 (incluye compras Databento: A 2,80 USD; C 75,78 USD).
- ADR-011 (ingesta GDELT por streaming) y ADR-012 (margen +5 min): propuestas.
- Revisión adversarial del informe G0 (en curso).
- Credenciales: guardadas solo en la sesión (`~/.config/wna/secrets.env`, fuera del repo). Recomendado regenerarlas (se compartieron en el chat) y ponerlas en la configuración del entorno.

## Bloqueos
- Sin aprobación de G0 no se compra precio intradía ni se inicia F2.
- cmegroup.com, BLS y DOL inaccesibles (403) desde el entorno; GDELT DOC API con rate limit.
- La sesión llegó al límite de uso a las ~05:10 UTC; se reanudó a las 07:25 UTC.
