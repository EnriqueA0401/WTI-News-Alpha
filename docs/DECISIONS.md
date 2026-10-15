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

## ADR-005 — cmegroup.com inaccesible desde el entorno (resuelta parcialmente)
- **Contexto:** la web de CME devuelve 403 / corta la conexión (anti-bot). No se pueden verificar tick, horario, pausa ni settlement. CLAUDE.md §4 prohíbe sustituir en silencio.
- **Opciones:** (a) el usuario sube WTI Fact Card + capítulo 200 del Rulebook NYMEX; (b) usar definiciones de instrumento de Databento (origen exchange); (c) ambas cruzadas.
- **Recomendación:** (c).
- **Consecuencias:** hasta resolverlo, `config/sessions.yaml` y `config/contracts.yaml` quedan `unverified`.
- **Actualización 2026-10-15:** el usuario subió WTI Fact Card (2012) y Rulebook NYMEX cap. 200 (vigente). Hashes en `manifests/cme_docs.json`; los PDF no se commitean (copyright CME, no redistribución). Verificados: 1.000 bbl, tick 0,01 USD/bbl, valor del tick 10 USD, regla de último día de negociación. Horario y settlement siguen sin verificar (ADR-010).

## ADR-006 — Persistencia de datos y captura prospectiva (pendiente de usuario)
- **Contexto:** el contenedor de trabajo es efímero; `data/` no va a git; la captura prospectiva de noticias (F2) necesita un proceso continuo.
- **Opciones:** almacenamiento en máquina del usuario / bucket / disco; captura en máquina del usuario, VPS o GitHub Actions cron.
- **Decisión (usuario, 2026-10-15):** los datos se guardan en la sesión en la nube.
- **Consecuencias:** el disco de la sesión se borra cuando el contenedor se recicla. Por eso: (1) cada descarga deja en git su manifiesto (fuente, request, fecha, sha256), de modo que las fuentes históricas re-descargables (GDELT, EIA, FRED/ALFRED, CFTC, NOAA, Databento) se pueden reconstruir y verificar por checksum; (2) la captura prospectiva de noticias (`first_seen_at`) no es re-descargable y una sesión no corre 24/7: se replanteará en F2 (propuesta: GitHub Actions cada 15 min guardando solo metadatos en una rama de datos).

## ADR-007 — Hallazgo de hypothesis: horas de pared inexistentes (aceptada)
- **Contexto:** el test de propiedades encontró que `ensure_utc` aceptaba `datetime(2016-03-13 02:00, America/New_York)`, hora que no existe (salto DST); Python la convierte sin avisar.
- **Decisión:** `ensure_utc`/`to_new_york` lanzan `CalendarError` ante horas en el hueco DST. Las ambiguas (vuelta de noviembre) también lanzan por defecto (resolverlas con `fold=0` adelantaría una hora un `available_at`); el llamador puede declarar `ambiguous="use_fold"` solo si `fold` proviene de una conversión fiable. Las horas publicadas por fuentes en ET se convierten con `ny_wall_to_utc`, que siempre es estricta. Tests de regresión añadidos.

## ADR-010 — Horario de sesión CL versionado por fecha y verificado empíricamente (aceptada, usuario 2026-10-15)
- **Contexto:** el Rulebook (200102.A) no fija el horario ("determined by the Exchange"). La Fact Card recibida es de 2012 y dice Globex 18:00–17:15 ET con pausa de 45 min, distinto de los valores de trabajo (cierre 17:00, pausa de 60 min). El horario puede haber cambiado dentro del periodo de estudio (2015–2026), y un horario erróneo invalidaría etiquetas que cruzan la pausa.
- **Opciones:** (a) fijar un único horario; (b) horario versionado por intervalos de fecha, derivado de las barras de 1 min reales (Databento) y contrastado con notices oficiales de CME cuando estén disponibles.
- **Decisión:** (b), aprobada por el usuario. En F1 se mide, por fecha, el primer y último minuto con negociación y la pausa diaria; cada cambio detectado se documenta. El settlement se verifica con datos de settlement oficiales (schema de estadísticas de Databento) o un documento de CME.
- **Consecuencias:** `config/sessions.yaml` seguirá `unverified` hasta F1; en F2+ pasará a tabla de vigencias por fecha.

## ADR-009 — Protocolo aprobado (aceptada)
- **Contexto:** cierre de F0; el usuario aprueba `docs/PROTOCOL.md` el 2026-10-15.
- **Decisión:** horizontes en tiempo de mercado (15 min, 60 min, 4 h, fin de sesión, 1 y 3 sesiones); fin de sesión = settlement NYMEX; métrica primaria = log loss de dirección 3 clases a 60 min (se formaliza en el pre-registro de F6); slippage ≥ 1 tick por lado; latencia 1 min; sensibilidad 1×/2×/3×; resto de A4–A12 según el protocolo.
- **Consecuencias:** en `config/` esos parámetros pasan a `approved`. Siguen bloqueados los valores externos sin verificar (ADR-005) y los que se fijan en F1/F6.

## ADR-008 — Revisión adversarial F0 (aceptada)
- **Contexto:** revisión independiente de `timeutils`, `config`, `source_check` y `runlog` (CLAUDE.md §10): 16 hallazgos, ninguno crítico.
- **Corregidos (con test de regresión):** horas de config sin comillas que YAML lee en base 60 (`WallTime` estricto); horas ambiguas resueltas en silencio (ADR-007); el gate de estado se podía saltar leyendo `.value` (ahora `unsafe_value` + test que lo prohíbe en `src/`); NaN/bool/texto/negativos aceptados en parámetros (tipos estrictos y rangos); claves YAML duplicadas (loader que las rechaza); clave de API visible en logs de httpx y en mensajes de error (logger httpx a WARNING + redacción en `JsonFormatter`); auth de fuentes incoherente y URLs con credenciales (validadores); traceback ausente en logs; campos libres que pisaban `ts`/`level`/`commit` (ahora bajo `data`); detalle de fallo ausente en el informe, redirecciones y `|` en tablas; `ny_wall_to_utc` con datetime; nulos en columnas UTC; validación semántica de horizontes y splits; errores no tipados (YAML, no-datetime); oráculo de hypothesis independiente.
- **Riesgos aceptados (bajos):** un 2xx solo prueba que el endpoint responde (un portal cautivo daría "ok"; el contenido se audita en F1); fechas anteriores a 1883 en NY usan offset LMT sin aviso (fuera del rango del proyecto, 2015+).

## ADR-011 — Ingesta histórica GDELT por filtrado en streaming (propuesta, pendiente de usuario)
- **Contexto:** el GKG 2015–2026 ocupa ≈ 2,9 TB comprimido (`reports/f1/gdelt.md` §3). Descargarlo entero supera los topes de CLAUDE.md §4 y no cabe en disco. Medido: ≈ 94 MB/s de descarga con 8 conexiones y ≈ 0,17 s de CPU por fichero.
- **Opciones:** (a) filtrar en streaming: descargar cada zip, verificar el md5 del índice maestro, conservar solo las filas que pasan un filtro amplio y versionado (temas/entidades de petróleo), registrar en el manifiesto el fichero de origen y la versión del filtro, y descartar el zip; (b) Google BigQuery (dataset público de GDELT; requiere cuenta GCP y tiene coste por TB escaneado); (c) solo Events/Mentions (≈ 10 GB/año, sin temas).
- **Propuesta:** (a). Primero el piloto 2024–2025 (≈ 400 GB de transferencia, ≈ 1,5–3 h); luego, si G1 lo justifica, el histórico completo (≈ 9–10 h). El filtro debe ser amplio (alta sensibilidad) porque la relevancia fina se decide en F3.
- **Consecuencias:** el "raw" de GDELT pasa a ser un extracto filtrado y reproducible (código + md5 de origen), no el zip original; queda documentado como excepción a CLAUDE.md §3.3. Requiere aprobación por tamaño y duración.

## ADR-012 — Margen de disponibilidad GDELT (propuesta, pendiente de usuario)
- **Contexto:** A9 aprobó `first_available_at = timestamp del fichero + 15 min`, a validar en F1. Evidencia (`reports/f1/gdelt.md` §4): en 2015–2026 los ficheros se publican entre 12 y 2 min **antes** de su timestamp nominal (máximo observado +0,3 min).
- **Opciones:** (a) mantener +15 min; (b) bajar a +5 min.
- **Propuesta:** (b). +5 min sigue cubriendo con holgura el peor caso observado (+0,3 min) y no penaliza tanto el horizonte de 15 min. Con (a) el horizonte de 15 min pierde gran parte de la reacción inicial.
- **Consecuencias:** cambia un parámetro aprobado: requiere aprobación (CLAUDE.md §4).
