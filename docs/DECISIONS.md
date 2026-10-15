# Registro de decisiones (ADR)

Formato: contexto · opciones · decisión · consecuencias. Estado: *aceptada*, *propuesta*, *pendiente de usuario*.

## ADR-001 — Numeración canónica de modelos (aceptada)
- **Contexto:** el Documento numera los modelos como M0–M6 (§9) y M0–M5 (§16.4).
- **Decisión:** canónica §9 (M0 naive, M1 lineal/logística, M2 XGB market-only, M3 +fundamentales, M4 +noticias estructuradas, M5 +embeddings, M6 TFT). Prueba central: M4 vs M3. Fijado por CLAUDE.md §2.2.
- **Consecuencias:** la tabla de §16.4 del Documento se ignora para la numeración.

## ADR-002 — Contenido de CLAUDE.md (aceptada)
- **Contexto:** el usuario pidió que CLAUDE.md sea copia exacta del prompt maestro; CLAUDE.md §14 pide un resumen con fase actual.
- **Opciones:** (a) dejarlo exacto y llevar el estado en `docs/PROGRESS.md`; (b) añadir al final una sección de estado.
- **Decisión (usuario, 2026-10-15):** (a). CLAUDE.md queda como copia exacta del prompt; el estado del proyecto se lleva en `docs/PROGRESS.md`.

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
- **Decisión (usuario, 2026-10-15):** los datos se guardan en la sesión en la nube.
- **Consecuencias:** el disco de la sesión se borra cuando el contenedor se recicla. Por eso: (1) cada descarga deja en git su manifiesto (fuente, request, fecha, sha256), de modo que las fuentes históricas re-descargables (GDELT, EIA, FRED/ALFRED, CFTC, NOAA, Databento) se pueden reconstruir y verificar por checksum; (2) la captura prospectiva de noticias (`first_seen_at`) no es re-descargable y una sesión no corre 24/7: se replanteará en F2 (propuesta: GitHub Actions cada 15 min guardando solo metadatos en una rama de datos).

## ADR-007 — Hallazgo de hypothesis: horas de pared inexistentes (aceptada)
- **Contexto:** el test de propiedades encontró que `ensure_utc` aceptaba `datetime(2016-03-13 02:00, America/New_York)`, hora que no existe (salto DST); Python la convierte sin avisar.
- **Decisión:** `ensure_utc`/`to_new_york` lanzan `CalendarError` ante horas en el hueco DST. Las ambiguas (vuelta de noviembre) también lanzan por defecto (resolverlas con `fold=0` adelantaría una hora un `available_at`); el llamador puede declarar `ambiguous="use_fold"` solo si `fold` proviene de una conversión fiable. Las horas publicadas por fuentes en ET se convierten con `ny_wall_to_utc`, que siempre es estricta. Tests de regresión añadidos.

## ADR-009 — Protocolo aprobado (aceptada)
- **Contexto:** cierre de F0; el usuario aprueba `docs/PROTOCOL.md` el 2026-10-15.
- **Decisión:** horizontes en tiempo de mercado (15 min, 60 min, 4 h, fin de sesión, 1 y 3 sesiones); fin de sesión = settlement NYMEX; métrica primaria = log loss de dirección 3 clases a 60 min (se formaliza en el pre-registro de F6); slippage ≥ 1 tick por lado; latencia 1 min; sensibilidad 1×/2×/3×; resto de A4–A12 según el protocolo.
- **Consecuencias:** en `config/` esos parámetros pasan a `approved`. Siguen bloqueados los valores externos sin verificar (ADR-005) y los que se fijan en F1/F6.

## ADR-008 — Revisión adversarial F0 (aceptada)
- **Contexto:** revisión independiente de `timeutils`, `config`, `source_check` y `runlog` (CLAUDE.md §10): 16 hallazgos, ninguno crítico.
- **Corregidos (con test de regresión):** horas de config sin comillas que YAML lee en base 60 (`WallTime` estricto); horas ambiguas resueltas en silencio (ADR-007); el gate de estado se podía saltar leyendo `.value` (ahora `unsafe_value` + test que lo prohíbe en `src/`); NaN/bool/texto/negativos aceptados en parámetros (tipos estrictos y rangos); claves YAML duplicadas (loader que las rechaza); clave de API visible en logs de httpx y en mensajes de error (logger httpx a WARNING + redacción en `JsonFormatter`); auth de fuentes incoherente y URLs con credenciales (validadores); traceback ausente en logs; campos libres que pisaban `ts`/`level`/`commit` (ahora bajo `data`); detalle de fallo ausente en el informe, redirecciones y `|` en tablas; `ny_wall_to_utc` con datetime; nulos en columnas UTC; validación semántica de horizontes y splits; errores no tipados (YAML, no-datetime); oráculo de hypothesis independiente.
- **Riesgos aceptados (bajos):** un 2xx solo prueba que el endpoint responde (un portal cautivo daría "ok"; el contenido se audita en F1); fechas anteriores a 1883 en NY usan offset LMT sin aviso (fuera del rango del proyecto, 2015+).
