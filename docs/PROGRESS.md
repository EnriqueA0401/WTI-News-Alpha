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
- Especificaciones CL verificadas con Rulebook cap. 200 (ADR-005). Horario/settlement: verificación empírica en F1 (ADR-010). ADR-006: datos en la sesión en la nube; captura prospectiva a replantear en F2.
- Credenciales: EIA, FRED y Databento verificadas (200) en esta sesión, guardadas fuera del repo en `~/.config/wna/secrets.env` (se pierden al terminar la sesión). Recomendado: regenerarlas (se compartieron en el chat) y ponerlas en la configuración del entorno.
- F1 — auditoría de fuentes (plan en `docs/plans/FASE_01.md`).

## Bloqueos
- cmegroup.com inaccesible desde el entorno (mitigado con documentos subidos; ADR-005).
- GDELT DOC API devuelve 429 (rate limit) desde el entorno.
- EIA/FRED/Databento requieren claves.
