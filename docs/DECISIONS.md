# Registro de decisiones (ADR)

Formato: contexto · opciones · decisión · consecuencias. Estado: *aceptada*, *propuesta*, *pendiente de usuario*.

## ADR-001 — Numeración canónica de modelos (aceptada)
- **Contexto:** el Documento numera los modelos como M0–M6 (§9) y M0–M5 (§16.4).
- **Decisión:** canónica §9 (M0 naive, M1 lineal/logística, M2 XGB market-only, M3 +fundamentales, M4 +noticias estructuradas, M5 +embeddings, M6 TFT). Prueba central: M4 vs M3. Fijado por CLAUDE.md §2.2.
- **Consecuencias:** la tabla de §16.4 del Documento se ignora para la numeración.

## ADR-002 — Contenido de CLAUDE.md (propuesta, pendiente de usuario)
- **Contexto:** el usuario pidió que CLAUDE.md sea copia exacta del prompt maestro; CLAUDE.md §14 pide un resumen con fase actual.
- **Opciones:** (a) dejarlo exacto y llevar el estado en `docs/PROGRESS.md`; (b) añadir al final una sección de estado.
- **Decisión propuesta:** (a). Prevalece la instrucción explícita del usuario (CLAUDE.md §2.1).

## ADR-003 — Python 3.12 (aceptada)
- **Contexto:** CLAUDE.md exige 3.11+. El entorno trae 3.13.
- **Decisión:** fijar 3.12 vía `uv` (`requires-python >=3.12,<3.13`): soporte amplio de wheels (XGBoost, PyTorch Forecasting en F11).
- **Consecuencias:** `make setup` instala 3.12 si falta.

## ADR-004 — Parámetros con estado y trazabilidad (aceptada)
- **Contexto:** "nada de números mágicos" y "verificar contra fuente oficial".
- **Decisión:** todo parámetro externo o de diseño en `config/*.yaml` es un `Param` con `status ∈ {verified, approved, unverified, proposed}` y `source_ref`. `Param.require()` lanza `UnverifiedValueError` salvo `verified`/`approved`.
- **Consecuencias:** ningún cálculo puede usar especificaciones de CL ni horizontes hasta verificarlos o aprobarlos.

## ADR-005 — cmegroup.com inaccesible desde el entorno (pendiente de usuario)
- **Contexto:** la web de CME devuelve 403 / corta la conexión (anti-bot). No se pueden verificar tick, horario, pausa ni settlement. CLAUDE.md §4 prohíbe sustituir en silencio.
- **Opciones:** (a) el usuario sube WTI Fact Card + capítulo 200 del Rulebook NYMEX; (b) usar definiciones de instrumento de Databento (origen exchange); (c) ambas cruzadas.
- **Recomendación:** (c).
- **Consecuencias:** hasta resolverlo, `config/sessions.yaml` y `config/contracts.yaml` quedan `unverified`.

## ADR-006 — Persistencia de datos y captura prospectiva (pendiente de usuario)
- **Contexto:** el contenedor de trabajo es efímero; `data/` no va a git; la captura prospectiva de noticias (F2) necesita un proceso continuo.
- **Opciones:** almacenamiento en máquina del usuario / bucket / disco; captura en máquina del usuario, VPS o GitHub Actions cron.
- **Decisión:** pendiente.

## ADR-007 — Hallazgo de hypothesis: horas de pared inexistentes (aceptada)
- **Contexto:** el test de propiedades encontró que `ensure_utc` aceptaba `datetime(2016-03-13 02:00, America/New_York)`, hora que no existe (salto DST); Python la convierte sin avisar.
- **Decisión:** `ensure_utc`/`to_new_york` lanzan `CalendarError` ante horas en el hueco DST; las ambiguas se aceptan porque `fold` las desambigua. Test de regresión añadido.
