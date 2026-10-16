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

## ADR-011 — Ingesta histórica GDELT por filtrado en streaming (aceptada: D5, usuario 2026-10-15)
- **Contexto:** el GKG 2015–2026 ocupa ≈ 2,9 TB comprimido (`reports/f1/gdelt.md` §3). Descargarlo entero supera los topes de CLAUDE.md §4 y no cabe en disco. Medido: ≈ 94 MB/s de descarga con 8 conexiones y ≈ 0,17 s de CPU por fichero.
- **Opciones:** (a) filtrar en streaming: descargar cada zip, verificar el md5 del índice maestro, conservar solo las filas que pasan un filtro amplio y versionado (temas/entidades de petróleo), registrar en el manifiesto el fichero de origen y la versión del filtro, y descartar el zip; (b) Google BigQuery (dataset público de GDELT; requiere cuenta GCP y tiene coste por TB escaneado); (c) solo Events/Mentions (≈ 10 GB/año, sin temas).
- **Propuesta:** (a). Primero el piloto 2024–2025 (≈ 400 GB de transferencia, ≈ 1,5–3 h); luego, si G1 lo justifica, el histórico completo (≈ 9–10 h). El filtro debe ser amplio (alta sensibilidad) porque la relevancia fina se decide en F3.
- **Consecuencias:** el "raw" de GDELT pasa a ser un extracto filtrado y reproducible (código + md5 de origen), no el zip original; queda documentado como excepción a CLAUDE.md §3.3. Requiere aprobación por tamaño y duración.

## ADR-012 — Disponibilidad de ficheros GDELT (decidida por el usuario 2026-10-15: se mantiene +15 min, ver ADR-014)
- **Contexto:** A9 aprobó `first_available_at = timestamp del fichero + 15 min`, a validar en F1. Evidencia (`reports/f1/gdelt.md` §4.3):
  - En slots normales el fichero se publica 2–12 min antes del nombre.
  - Los ficheros degradados llegan hasta +2 min tarde.
  - El primer fichero tras una caída puede llegar **hasta +80 min tarde** (5 de 489 superan +15 min).
- **Opciones:** (a) +15 min fijo; (b) +5 min fijo (descartado: lo invalidan los casos tardíos); (c) `max(nominal + 15 min, Last-Modified + 5 min)`, registrando Last-Modified en cada descarga.
- **Propuesta:** (c). Conserva el margen aprobado y cubre las subidas tardías. Si Last-Modified reflejara una re-subida posterior, solo retrasa la disponibilidad (más conservador).
- **Consecuencias:** F2 guarda el header Last-Modified en el manifiesto. Cambia la definición de un timestamp: requiere aprobación (CLAUDE.md §4).

## ADR-013 — Filtro GKG versionado, columnas conservadas y flujo de publicación (aceptada, implementa ADR-011)
- **Contexto:** D5 aprobado: ingesta del GKG por streaming con un filtro amplio y versionado. Lo descartado solo se recupera volviendo a descargar.
- **Medido en la muestra F1 (175.851 líneas):**
  - GCAM ocupa el 70 % de los bytes de cada fila.
  - Un día real (2024-06-10, 96 ficheros, filtro v1) dio 142.570 filas, de las que se conservaron 4.107. El Parquet ocupa 13,6 MB, lo que da unos 5 GB/año.
- **Decisión: filtro `gkg-oil-v2`.** Se conserva una fila si cumple alguna de estas condiciones:
  - un tema contiene un token de petróleo o combustible entre `_` (lista en `config/gdelt.yaml`), excluyendo `TAX_FOODSTAPLES_*`;
  - el título coincide con crudo, petrol, OPEC, WTI, Aramco, etc.;
  - la organización es OPEC o Aramco;
  - la URL contiene esos términos entre separadores no alfabéticos.

  Conserva el 4,4 % de las filas de la muestra F1. La URL añade 116 filas (58 de ellas en 2015, que no tiene títulos).
- **Columnas:** se guardan las 27 columnas originales (GCAM incluida), más `source_file_ts`, `row_in_file` y los cuatro motivos de coincidencia.
- **Validación de filas:**
  - 27 campos y GKGRECORDID al inicio;
  - la línea siguiente también debe ser un registro bien formado; si no, la línea actual puede ser un registro partido y se rechaza;
  - los rechazos se cuentan.
- **Umbrales:** el fichero falla si rechaza más de 2 líneas y más del 2 %; el día falla si rechaza más del 0,5 % o tiene más de 8 ficheros 404.
- **Flujo:**
  - los procesos de trabajo escriben en `data/staging`;
  - el proceso principal, con lock exclusivo, enlaza la salida en raw y la registra;
  - si una ejecución muere entre ambos pasos, la siguiente regenera el día y adopta la salida solo si el sha256 coincide.
- **Procedencia en cada línea del manifiesto:** `filter_sha256`, índice usado (ruta, sha256 y último fichero), versión de Polars, `Last-Modified`, reemplazos UTF-8 y conteos.
  - Un cambio del filtro sin nueva `version` hace fallar la verificación.
  - `end` no puede pasar del día del último fichero del índice.
- **Revisión adversarial (14 hallazgos):** corregidos los hallazgos 1–7 y 9–14, con test de regresión.
  - **Riesgo aceptado (hallazgo 8, bajo):** las líneas rechazadas no se guardan. Son 2 de 175.851 en la muestra y se cuentan en el manifiesto.
  - **Nota:** `gkg_record_id` no es único en 2015–2018; la clave de fila es `(source_file_ts, row_in_file)`.
- **Consecuencias:**
  - v1 solo se usó en la prueba de un día (2024-06-10, se conserva como registro); el piloto usa v2.
  - Con GCAM, el histórico completo podría no caber en el disco de la sesión. La decisión de quitarlo se tomará con las cifras del piloto.

## ADR-014 — Decisiones de la puerta G0 (usuario, 2026-10-15)
Respuestas del usuario a D1–D12 (`reports/F1_source_audit.md` §5) y a la elección de proveedor.

| # | Decisión del usuario | Consecuencia registrada |
|---|---|---|
| D1 | Precio: **Darwinex como guía** (intradía) | Desviación del Documento (que pide NYMEX CL). Se documenta como limitación (ver abajo). |
| D2/D3 | **No se compra nada a Databento** | Sin datos oficiales de CL por contrato ni verificación de sesiones y settlement con `status`/`statistics`. |
| Proveedor | **Darwinex**; el usuario dará acceso con credenciales | Credenciales solo como variables de entorno de la configuración del entorno, nunca en el chat ni en el repo. |
| D4 | Ventana principal **2020–2026**; 2015–2019 secundaria | — |
| D5 | Ingesta GDELT por streaming (aprobada antes) | Piloto 2024–2025 hecho (`reports/f2/gdelt_gkg_pilot.md`). |
| D6 | **Mantener `first_available_at = nominal + 15 min`** (se rechaza la regla con `Last-Modified`) | El piloto midió 2 de 68.493 ficheros publicados después de nominal + 15 min (+35,7 y +20,2 min). Con esta regla esos ficheros serían visibles antes de existir: riesgo de leakage pequeño pero no nulo (CLAUDE.md §3.1). Se ha comunicado al usuario. |
| D7 | **No descargar el archivo histórico de la EIA** | Los fundamentales EIA vienen de la API con valores actuales (revisados), no de primera publicación. Por CLAUDE.md §3.2 esas features se marcan **"riesgo de revisión"**, lo que afecta a M3 y M4 por igual. |
| D8 | WPSR en miércoles normales: **10:30 ET** (riesgo declarado); festivos y desplazados: R-PIT | Se validará con la captura prospectiva (D12). |
| D9 | **R-PIT** para horas no verificadas | Fecha verificada sin hora: 18:00 ET. Fecha dudosa: día hábil siguiente o exclusión. ALFRED: 00:00 ET del día siguiente. |
| D10 | DTWEXBGS **solo desde 2019-02-04** | — |
| D11 | NOAA **fuera de las features** (solo contexto) | — |
| D12 | **Captura prospectiva con GitHub Actions**, a diseñar en F2 | — |

**Limitaciones que introduce D1 (Darwinex):**
- El precio es un CFD del bróker, no el futuro NYMEX CL. Lo fija el bróker, con su spread y sus ajustes en los vencimientos.
- Los horarios de sesión son los del bróker.
- La pregunta del Documento queda respondida "sobre el CFD de WTI de Darwinex". Así se dirá en todos los informes.
- G0 no puede cerrarse hasta auditar los datos de Darwinex: cobertura, zona horaria del servidor, huecos, rollover y licencia.

## ADR-015 — Contraste de Darwinex con el futuro CL diario (aceptada, usuario 2026-10-15/16)
- **Contexto:** el usuario pide contrastar los datos de Darwinex (CFD) con los precios diarios del futuro de petróleo, para asegurar su fiabilidad.
- **Hecho:**
  - Descargadas de la API de la EIA (gratis, con manifiesto) las liquidaciones diarias NYMEX CL de los contratos 1 a 4 (RCLC1–RCLC4).
  - Cobertura: 2015-01-02 → **2024-04-05**, 2.337 días por serie, sin huecos de más de 5 días naturales.
  - Incluye el −37,63 del 2020-04-20 (dato real).
  - La API confirma que la serie termina ahí (`endPeriod` = 2024-04-05): la EIA dejó de publicarla.
  - Auditoría: `scripts/f1_audit/eia_futures_audit.py`, `reports/f1/eia_futures_coverage.csv`.
- **Método previsto:**
  - Cierre diario de Darwinex a la hora de liquidación (14:30 ET) frente a RCLC1 y RCLC2.
  - Se mide la diferencia en USD y en %, la correlación de los retornos diarios y los saltos en los vencimientos (para identificar qué contrato sigue el CFD y cuándo rola).
  - Se fijará un umbral de aceptación antes de mirar los datos de Darwinex.
- **Hueco:** 2024-04-06 → 2026 queda sin referencia de futuros gratuita y verificada.
  - Stooq no es accesible desde el entorno.
  - Yahoo (CL=F, serie continua) sí responde, pero sus términos de uso son dudosos → **requiere decisión del usuario** (CLAUDE.md §4).
  - Alternativa sin coste ni dudas: contrastar con el spot DCOILWTICO (FRED), que no es el futuro.
- **Decisión del usuario:** contrastar solo hasta el 2024-04-05 con la EIA. Desde 2024-04-06 los datos de Darwinex quedan **sin contraste externo**; es una limitación declarada en los informes (parte del periodo principal 2020–2026).

## ADR-016 — Captura prospectiva con GitHub Actions (aceptada: D12, usuario 2026-10-15)
- **Decisión:** el workflow `capture` corre cada 5 min y registra la primera aparición de:
  - ficheros GDELT: anunciado en `lastupdate.txt` y disponible de verdad;
  - el WPSR de la EIA: semana de `table1.csv`, `Last-Modified` y ventana de publicación de la URL firmada de CloudFront;
  - el COT de CL de la CFTC: `report_date` y `:created_at`.
- Todo sin claves. Se guarda en la rama huérfana `capture-data` (append-only).
- La disponibilidad real queda acotada por `(previous_check_utc, observed_at_utc]`.
- **Hallazgo en la primera prueba (2026-10-16):** desde el entorno de desarrollo, `lastupdate.txt` anunciaba los GKG de 00:30 y 00:45 mientras el fichero daba 404 (00:30–00:35 UTC). Desde GitHub, el GKG de 00:45 ya estaba disponible a las 00:36. Por eso se separan los eventos "anunciado" y "disponible"; la captura medirá cuánto duran estos desfases.
- **Prueba en GitHub:** run 38830378719 (manual) OK. Rama `capture-data` creada con 8 observaciones.
  - Consecuencia para F3/F4: la hora de anuncio no garantiza que el fichero exista.
  - La disponibilidad histórica sigue la regla D6 (+15 min) y lo verifica esta captura.
- **Riesgos:**
  - el cron de GitHub se retrasa;
  - GitHub desactiva los programados tras 60 días sin actividad;
  - el repo es público, así que las observaciones también (solo metadatos de fuentes públicas).
