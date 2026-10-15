# F0 — Protocolo y esqueleto

**Objetivo:** repositorio reproducible, `make check` verde y protocolo sin ambigüedades.

**Entradas:** CLAUDE.md, Documento v1.1.

**Salidas:** `pyproject.toml` + `uv.lock`; carpetas §4.1; Makefile; pre-commit; CI; `errors.py`, `timeutils.py`, `config.py` + `config/*.yaml`, `runlog.py`, `cli.py`, `ingestion/source_check.py`; `docs/PROTOCOL.md`, `PROGRESS.md`, `DECISIONS.md`, `DATA_DICTIONARY.md`; `runs/registry.csv`; `reports/source_access.md`.

**Interfaces:**
- `ensure_utc(datetime) -> datetime` · `to_new_york(datetime) -> datetime` · `ny_wall_to_utc(date, time) -> datetime` · `require_utc_column(pl.DataFrame, str) -> None`
- `Param[T].require(name) -> T` · `load_project_config(dir) -> ProjectConfig`
- `classify(code, body, key_present) -> AccessStatus` · `probe(...)` · `render_markdown(...)`
- `run_step(step, logger, commit, **fields)` (context manager)

**Invariantes críticos:** timestamps naive → `NaiveTimestampError`; horas de pared inexistentes/ambiguas sin `fold` → `CalendarError`; parámetros no verificados/aprobados no utilizables; claves nunca en informes/logs.

**Riesgos:** especificaciones CME no verificables desde el entorno; persistencia de datos.

**Tests:** casos DST a mano (mar/nov 2026), propiedades `hypothesis` de preservación de instante, validación de config, clasificación de sondas, no filtrado de claves, CLI.

**Hecho cuando:** `make check` verde y protocolo aprobado por el usuario.
