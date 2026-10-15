# Progreso

**Fase actual:** F0 cerrada (protocolo aprobado 2026-10-15). Siguiente: F1 — auditoría de fuentes.

## Hecho
- `CLAUDE.md` (prompt maestro) y `docs/WTI_News_Alpha_v1.1.pdf` (Documento) en el repo.
- Esqueleto: `uv` + Python 3.12, paquete `wti_news_alpha` (`src/`), carpetas §4.1, Makefile, pre-commit, workflow CI (`make check`).
- Núcleo: `errors.py`, `timeutils.py` (UTC, DST, rechazo de naive y de horas inexistentes), `config.py` + `config/*.yaml` (parámetros con estado), `runlog.py` (logs JSON con commit), CLI `wna` (`sources check`; comandos futuros fallan explícitamente).
- `make check` en verde: ruff, mypy --strict, 120 tests, cobertura de ramas 99 %.
- Revisión adversarial independiente: 16 hallazgos resueltos o aceptados como riesgo bajo (ADR-008).
- `reports/source_access.md`: acceso de red por fuente.

## Pendiente
- Decisiones de usuario: ADR-005 (CME). ADR-006: datos en la sesión en la nube; captura prospectiva a replantear en F2.
- Credenciales en la configuración del entorno: `EIA_API_KEY`, `FRED_API_KEY`, `DATABENTO_API_KEY` (las compartidas en el chat deben regenerarse).
- F1 — auditoría de fuentes (plan en `docs/plans/FASE_01.md`).

## Bloqueos
- cmegroup.com inaccesible desde el entorno (ADR-005).
- GDELT DOC API devuelve 429 (rate limit) desde el entorno.
- EIA/FRED/Databento requieren claves.
