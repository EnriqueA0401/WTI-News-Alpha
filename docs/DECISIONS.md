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
- **Revisión adversarial (2026-10-16, 11 hallazgos), todos corregidos con test de regresión:**
  1. **(alto)** un formato inesperado en una fuente tiraba toda la ejecución. Ahora se registra como error de esa fuente y las demás siguen.
  2. **Cotas:**
     - `observed_at_utc` pasa a ser la respuesta recibida (cota superior);
     - se añade `request_started_utc`;
     - `previous_check_utc` es el inicio del chequeo anterior con éxito;
     - `lower_bound_valid` es falso cuando no hay chequeo previo, o cuando un `|available` se ve en la misma ejecución que su anuncio (el fichero puede existir antes de anunciarse: se observó unos 50 s antes).
  3. **Ficheros GDELT pendientes:** los anunciados con 404 se siguen comprobando hasta que existen, o hasta 24 h (`|expired`). Los saltos de ficheros se registran como `gap|desde|hasta`.
  4. **Claves con los campos que cambian:**
     - GDELT: `|listed|<md5>`;
     - EIA: `semana|Last-Modified|DateGreaterThan`;
     - COT: `report_date|created_at`.
  5. **Política CloudFront:** se guarda como `cf_policy_date_greater_than_utc` y `cf_policy_date_less_than_utc`. Su significado (¿hora de publicación?) no está verificado: en la semana festiva valía 12:00 ET, y el `Last-Modified` del fichero era anterior.
  6. **Ficheros y estado:**
     - mes del fichero = mes de cada observación;
     - estado validado con pydantic;
     - URLs y límites en `config/capture.yaml`;
     - log `run_step`.
  7. **Workflow:**
     - un fallo de `git ls-remote` ya no crea una rama vacía;
     - `run_id` incluye el intento.
- **Riesgo aceptado (bajo):** si una ejecución muere entre escribir observaciones y estado, la siguiente puede repetir ítems. Aguas abajo se toma la primera aparición.
- **Pendiente de verificar:** que el cron programado de GitHub se dispare. A las 01:27 UTC aún no había corrido ninguno.

## ADR-017 — Datos Darwinex XTI recibidos por el usuario (descripción declarada, pendiente de verificar)
- **Descripción del usuario (2026-10-16), aún no verificada por nosotros:**
  - Barras M1 exportadas de StrategyQuant X (SQX), un fichero de ≈ 600 MB.
  - OHLC sobre el **bid**. `TickVolume` = nº de ticks del minuto. `AvgSpread` = spread medio (ask − bid) del minuto.
  - Hora del **servidor del bróker**, sin convertir a UTC: el primer tick cae el lunes a las 01:00.
  - Minutos sin ticks: no hay barra (no se rellenan).
  - Sin precios inválidos, ticks desordenados ni ask < bid.
  - **Hueco: ningún tick entre 2020-12-31 y 2024-01-05** (del archivo original). El resto de huecos son fines de semana y festivos.
- **Consecuencias:**
  1. El hueco deja sin precio 3 de los 7 años del periodo principal (2021–2023, D4). Hay que re-descargarlos en SQX antes de usar los datos; sin ellos, el estudio intradía tendría solo 2020 y 2024–2026.
  2. **Zona horaria:** no se supondrá ninguna. Se inferirá el desfase servidor→UTC por fecha (incluidos los cambios de horario) desde los datos:
     - la pausa diaria del CL (17:00–18:00 ET) debe verse como minutos sin ticks;
     - se contrastará con el cierre diario frente a la liquidación de la EIA (14:30 ET, hasta 2024-04-05).
     - Si no es concluyente → parada y pregunta.
  3. **Precio de referencia para etiquetas:** medio aproximado = bid + AvgSpread/2 (a fijar en F6). Costes: AvgSpread real por minuto (mejor que el supuesto de 1 tick).
  4. Las barras ausentes nunca se rellenan (CLAUDE.md §6.3).
- **Pendiente del usuario:** enlace de descarga (Drive/Dropbox) y re-descarga de 2021–2023 en SQX.
- **Primer intento de descarga (2026-10-16):** Drive devolvió la página de inicio de sesión (fichero no compartido públicamente).
  - Queda en raw como `darwinex/xti_m1_sqx_20261016.bin` (HTML, 947 KB, manifiesto `manifests/darwinex.jsonl`) y se marca **inválido**; no se borra porque raw es inmutable.
  - La siguiente descarga irá a otra ruta.
- **Auditoría (2026-10-16, `reports/f1/darwinex.md`):**
  - Datos válidos: 1,96 M barras, sin errores de formato.
  - Hueco 2021–2023 confirmado.
  - Zona horaria inferida = America/New_York + 7 h (pausa diaria siempre 00:00–00:59 servidor, también en los cambios de horario).
  - Frente a la EIA: diferencia mediana de 0,06–0,11 USD y correlación de retornos de 0,979 con C1. Muchos días sigue al 2.º vencimiento → hay rolls propios del bróker que habrá que detectar.
- **Roll del CFD (2026-10-16):** no hay escalón de roll. El precio pasa gradualmente de C1 a C2 durante el mes (w ≈ 0,2 a 22 días hábiles del vencimiento → ≈ 1 a 3 días).
  - Se trata como precio a vencimiento casi constante.
  - Las etiquetas no cruzan la pausa diaria, donde se concentran los saltos (mediana de 6,9 pb frente a 0,5 pb).

## ADR-018 — Arranque con 2018–2020 + 2024–2026 y filtro `gkg-oil-v3` sin GCAM (usuario, 2026-10-16)
- **Contexto:** falta el precio de 2021–2023 (ADR-017). El usuario decide no esperar.
- **Decisión 1:** el periodo de trabajo pasa a ser 2018–2020 + 2024–2026, con precio Darwinex y noticias GDELT. 2021–2023 se añadirá cuando llegue.
  - Esto modifica D4 (2020–2026).
  - Limitación declarada: periodo discontinuo; 2018 sin títulos.
- **Decisión 2:** en la ampliación se deja de guardar la columna GCAM (≈ 70 % del tamaño).
  - Va como filtro nuevo `gkg-oil-v3`: mismas filas que v2, columna `v2_gcam` excluida (`drop_columns`).
  - 2024–2025 queda en v2 (con GCAM). Aguas abajo se usa la intersección de columnas.
- **Ejecución:** `wna ingest gdelt-gkg` 2026-01-01 → 2026-10-15 y 2018-01-01 → 2021-01-01 (≈ 2–3 h, aprobado por el usuario).
- **Ejecución 2018–2020 (2026-10-16):** 1.095 de 1.096 días OK. Falló 2020-05-19 por un único registro (página de patentes de la USPTO) con 48 saltos de línea internos: 49 líneas de 1.220, por encima del 2 % por fichero. El md5 coincide con el índice, así que viene así de origen.
  - **Corrección:** el umbral por fichero cuenta registros rotos (tramos de líneas rechazadas consecutivas, `rejected_blocks`), no líneas.
  - Los días ya escritos no cambian: con la regla nueva solo pueden pasar más días, nunca menos.
  - El umbral diario sigue en líneas (0,5 %).
  - Test de regresión añadido. Se re-ejecuta ese día.

## ADR-019 — G0 aprobado; F2 continúa con precio, fundamentales y noticias (usuario, 2026-10-16)
- **Decisión del usuario:** "Aprobado G0" sobre `reports/G0_cierre.md`: **intradía viable (condicionado)** con el CFD XTIUSD de Darwinex en 2018–2020 + 2024–2026.
- **Consecuencias:**
  - Los valores de Darwinex inferidos de los datos en F1 pasan a estado `approved` en `config/darwinex.yaml`:
    - hora servidor = NY + 7 h;
    - sello = inicio de barra;
    - pausa diaria 17:00–18:00 ET.
  - No hay documentación del bróker. Si aparece, se contrasta.
  - Las limitaciones de G0 (§"Limitaciones declaradas") se arrastran a todos los informes.
- **Siguiente:** resto de F2 en tres bloques, cada uno con su plan:
  1. precio normalizado (`docs/plans/FASE_02_precio.md`);
  2. fundamentales;
  3. noticias con `first_available_at`.
  Después, la puerta G1.
- **Extracción del CSV de Darwinex** registrada en `manifests/darwinex_extract.jsonl`: miembro del RAR, sha256 del padre y del hijo. Re-extraer con `bsdtar -xO` reproduce el sha256.

## ADR-020 — Aplicación de D7–D9 al calendario WPSR y publicador común de capas normalizadas (2026-10-16)
- **Contexto:** el usuario ya decidió las reglas (ADR-014 D7, D8, D9). Falta concretarlas sobre las fuentes F1 sin inventar reglas nuevas.
- **Decisiones técnicas (aplicación, no reglas nuevas):**
  1. **Semana → primera publicación:** la primera fecha del índice del archivo ≥ viernes + 5 días.
     - Validado sobre los datos reales: 612 publicaciones 2015–2026, todas con ≥ 1 semana; retraso máximo de 12 días.
     - Solo 2022-06-29 y 2023-11-15 reciben 2 semanas: son las semanas sin publicación propia de F1.
     - Las 41 páginas "week ending" del archivo coinciden y las 14 excepciones oficiales también.
  2. **"Miércoles normal" (D8)** = miércoles con retraso de 5 días que no está en la tabla de excepciones.
     - Evidencia: en dic-2024 → nov-2026 todas las excepciones oficiales son no-miércoles, incluidas las semanas de Thanksgiving, Juneteenth y 4 de julio.
  3. **Fecha publicada solo en el archivo** (2020-01-03, que no está en FRED 212): se trata como fecha no verificada → 18:00 ET del siguiente laborable (R-PIT 2).
     - "Laborable" = lunes a viernes. No hay un calendario federal verificado: opm.gov da 403.
  4. **Publicador común** (`normalization/store.py`):
     - directorio por versión, escrito una vez y en solo lectura;
     - manifiesto atómico con sha256 de entradas y salidas y con los parámetros de configuración;
     - cambiar la configuración exige una versión nueva.
     - Por eso el precio pasa a `dwx-xti-m1-v2`, con los mismos cálculos más las validaciones de la revisión adversarial. La v1 se conserva.
- **Consecuencias:**
  - Las publicaciones desplazadas de 2015–2024 pierden la reacción intradía (18:00 ET), como ya se aceptó en D9.
  - Todo valor WPSR lleva `revision_risk` (D7). WCESTUS1 antes de 2016-10-07 lleva además `known_redefinition`.

## ADR-021 — Revisión adversarial del WPSR y calendario federal para el COT (2026-10-16)
- **Revisión adversarial del WPSR (`eia-wpsr-v1`):** 10 hallazgos, sin fugas en la salida publicada. Corregidos en `eia-wpsr-v2`; la v1 se conserva.
  1. **(alto)** El parser de la tabla de excepciones perdía 3 de 14 filas: una celda con clase y dos filas `<tr>` sin clase. Ahora se usa un parser HTML y falla si el número de filas no coincide con las cabeceras.
     - La publicación del lunes 2025-12-29 pasa de 18:00 (R-PIT) a 17:00 ET (oficial).
  2. **(alto)** Las 2 publicaciones compartidas (2022-06-29 y 2023-11-15) recibían 10:30 ET. Incluyen una semana desplazada, así que ahora van a R-PIT (18:00 ET). Aplica D8: "festivos y desplazados → R-PIT".
     - Por eso "miércoles normal" exige una sola semana con retraso de 5 días (corrige ADR-020 §2).
  3. El publicador guarda el hash del código de transformación. Cambiar el código sin cambiar la versión → error.
     - Si el manifiesto existe sin directorio, se reconstruye y solo se publica si reproduce los hashes.
     - Campos extra reservados → error.
  4. Tests de propiedades (hypothesis) de la asignación semana → publicación, más un test con el calendario real (14 excepciones).
  5. Guardia anti-leakage más fuerte:
     - `release_date ≥ semana + 5 días`;
     - `available_at` (fecha NY) ≥ `release_date`.
  6. Validaciones de entrada:
     - hora de 12 h en rango;
     - excepciones duplicadas → error;
     - "Release Date" de cada página comparada con su fecha;
     - año de la carpeta del índice coherente.
  7. El manifiesto cuenta las filas descartadas por serie y motivo.
  - Por el punto 3 (código en el manifiesto) el precio pasa a `dwx-xti-m1-v3`, con el mismo cálculo.
- **Calendario federal** (`normalization/federal_calendar.py`):
  - festivos de 5 U.S.C. 6103, contrastados con el texto oficial (govinfo);
  - cierres por orden ejecutiva del Federal Register 2014–2025 (9 órdenes).
  - Hay 4 cierres que la regla de F1 no tenía: 24-dic de 2018, 2019 y 2024, y 24/26-dic-2025. En F1 adelantaban fechas (dirección de leakage).
  - Domingo → lunes y el medio día de 2015-12-24 cuentan como no hábiles (conservador).
  - Incluye Inauguration Day (área de Washington, sede de CFTC y EIA).
- **COT (`cftc-cot-v1`, código listo, sin publicar):** dos parámetros de hora quedan en `proposed` hasta que decida el usuario:
  - margen sobre `created_at` de la API (desde oct-2022);
  - exclusión de la puesta al día de 2019 (as-of 2018-12-31 → 2019-02-26).

## ADR-022 — Decisiones del usuario sobre la hora del COT (2026-10-16)
- **Margen sobre `:created_at` de la API (as-of desde 2022-10-04):** **+0 min**. Se usa la hora exacta en que la CFTC subió la fila.
  - Riesgo aceptado: el tiempo que tarda en propagarse un dato desde la API hasta el mercado. La propuesta era +5 min.
- **Puesta al día tras el cierre de 2018-19** (as-of 2018-12-31 → 2019-02-26, 9 semanas): se usa **el calendario previsto** por la CFTC (PR 7864-19: uno el martes y otro el viernes desde el 2019-02-01) **+ 1 día hábil** (18:00 ET, R-PIT 2).
  - Fechas previstas calculadas a mano en `config/cftc_cot.yaml`.
  - Riesgo aceptado: si la CFTC se retrasó más que lo previsto, esas semanas estarían disponibles antes de tiempo. Se recomendaba excluirlas.
- Ambos parámetros pasan a `approved`. `cftc-cot-v1` puede publicarse.

## ADR-023 — Revisión adversarial del COT y del calendario federal (2026-10-16)
7 hallazgos, ninguno crítico. Comprobaciones superadas:
- las 210 horas de la API son ≥ regla y ≥ 15:30 ET;
- la regla coincide 52/52 con el calendario oficial 2026 y 12/13 con la tabla 2025 (la 13.ª usa la API, que es más tardía);
- columnas zip = API en 614 × 14 celdas.

Corregido en `cftc-cot-v2` (la v1 no llegó a publicarse):
1. **2019-02-01 y el primer informe normal (as-of 2019-03-05) también eran fechas "previstas"** (PR 7864-19: "expects").
   - Pasan al tratamiento de la puesta al día: fecha prevista + 1 día hábil, 18:00 ET.
   - Es la decisión del usuario de ADR-022 extendida a esas 2 semanas, lo que la hace más conservadora.
2. `created_at` sin zona horaria → `NaiveTimestampError`.
3. Una fila de la API sin publicación verificada no puede ser anterior a la regla oficial (fecha y 15:30 ET) → error.
4. **Riesgo residual (bajo, aceptado):** solo se modelan los cierres por orden ejecutiva. Los cierres de OPM por nieve en DC no están. Ejemplo: el cierre de OPM del 2025-01-06 no retrasó el COT.
5. Un as-of en lunes (martes festivo) no se trata como desplazamiento: el festivo ya está en la regla. Lo confirman la tabla 2025 y la API 2023-07-07.
6. Fechas de configuración sin as-of en los datos → error. También se comprueba que no se pierden filas en el join.
7. Tests añadidos:
   - casos a mano (Inauguration Day 2017, lunes 2017-07-03);
   - propiedades;
   - datos reales contra el calendario oficial 2026;
   - calendario federal con cierres.

## ADR-024 — FRED/ALFRED: historial de vintages y fechas de vintage anteriores a la observación (2026-10-16)
- **Datos:** historial completo de vintages ALFRED (`output_type=1`) de DGS10, DGS2, T10YIE, VIXCLS y DTWEXBGS. Se descargó en ventanas anuales de tiempo real para no superar el límite de 2000 vintages.
  - DTWEXBGS empieza en su primera vintage, 2019-02-04 (D10). Antes de esa fecha la API da HTTP 400.
  - Raw con manifiesto; la clave está redactada y se comprobó que no aparece en ningún manifiesto.
- **Regla (D9 / R-PIT 4):** `available_at` = 00:00 ET del día siguiente a la fecha de vintage.
- **Hallazgo:** en 32 valores de VIXCLS (festivos federales con mercado abierto, como Columbus Day, o lunes festivos), ALFRED fecha la vintage **un día hábil antes de la observación**.
  - Ejemplo: el VIX del lunes 2018-10-08 (15,69, distinto del viernes) tiene vintage del 2018-10-05.
  - Aplicar la regla tal cual sería leakage. Se aplica una cota inferior: `available_at` = 00:00 ET del día siguiente a max(vintage, observación).
  - Las filas se marcan con `vintage_before_obs`.
  - Consecuencia: la fecha de vintage de ALFRED no garantiza ser la primera publicación. Para series diarias de mercado, el día de la observación es el límite seguro.
- Los valores "." se conservan como nulos, para que un borrado posterior no deje vivo el valor anterior.
- En la primera ventana (2015-01-01) los periodos previos quedan recortados y se marcan con `vintage_start_clipped`. Es conservador.

## ADR-025 — Revisión adversarial de FRED/ALFRED (2026-10-16)
9 hallazgos, ninguno crítico. Corregidos en `fred-alfred-v2`; la v1 se conserva.
1. **(alto)** T10YIE tiene vintage el mismo día de la observación en 1.817 casos (desde 2019-H2). Con R-PIT 4 quedaría disponible a las 00:00 ET del día siguiente, 16 h antes de H.15.
   - Ahora se aplica, además, **la hora oficial del calendario de cada serie** (páginas vigentes de la Fed, `reports/f1/fred.md` §4):
     - H.15 (DGS10, DGS2 y T10YIE, que F1 recomendaba tratar "no antes que H.15"): siguiente día hábil federal a las 16:15 ET;
     - H.10 (DTWEXBGS): lunes siguiente a la semana (o siguiente hábil) a las 16:15 ET;
     - VIXCLS: 00:00 ET del día siguiente a la observación.
   - `available_at` = máximo de R-PIT 4 y esa hora. Solo puede retrasar disponibilidades, nunca adelantarlas.
   - Se marca `floored_by_schedule`.
   - **Limitación:** las horas de la Fed salen de las páginas vigentes; no están verificadas para todo 2015–2026.
2. **Corrección de ADR-024:** los 32 casos de VIXCLS con vintage anterior a la observación son sobre todo festivos con el mercado cerrado (4 de julio, Thanksgiving…), no solo festivos con mercado abierto.
   - Riesgo residual: que una revisión se feche un día antes. No es detectable con ALFRED.
3. Se añade `superseded_at` (= `available_at` de la vintage siguiente). La consulta point-in-time es `available_at ≤ t < superseded_at`. `vintage_end` no debe usarse para eso.
4. Si varias vintages caen en el mismo instante, solo queda visible la última. Las demás se cuentan (`hidden_same_instant_vintages`) y se exige que `available_at` crezca estrictamente.
5. Validaciones:
   - valores no finitos → error;
   - `observation_start` igual en todas las ventanas;
   - año del fichero = ventana.
6. DTWEXBGS: su primera ventana (2019-02-04) es la primera vintage real, no un recorte.
   - Interpretación de D10: obs anteriores a 2019-02-04 se incluyen con la disponibilidad de esa vintage (correcta point-in-time).
7. Fusión de ventanas vectorizada (CLAUDE.md §13). Test de propiedades: cortar la historia en ventanas no cambia la fusión.

## ADR-026 — Noticias: ficheros GDELT tardíos y fecha de publicación (usuario, 2026-10-16)
- **Revisión adversarial de `gdelt-news-v1`:** sin fallos de código críticos. Dos hallazgos de datos necesitaban decisión del usuario.
- **Decisión 1: ficheros tardíos.** La evidencia de D6 estaba incompleta: había **33 ficheros** publicados más de 15 min después de su nombre (2 en 2024–2025 y 31 en 2018–2020), con 2.875 noticias. El peor llegó 5,8 días tarde (2018-02-21 20:15).
  - El usuario elige **"usar la hora real"**: `first_available_at` = max(nominal + 15 min, `Last-Modified`).
  - Esto modifica D6 solo para esos ficheros. Se mantiene `late_file`.
  - Un fichero con filas y sin `Last-Modified` → error.
  - `Last-Modified` puede ser una re-subida. En ese caso solo retrasa la disponibilidad, que es la dirección conservadora (ADR-012).
- **Decisión 2: fecha de publicación del artículo** (`PAGE_PRECISEPUBTIMESTAMP`, huso no verificado). El 1,9 % llega "en el futuro", casi siempre por la hora local del medio (Europa y Asia).
  - El usuario elige **"solo imposibles"**: se excluye solo si la fecha va más de 14 h por delante de `first_available_at` (`published_after_available`) o si el año es < 1995 (`published_at_incoherent`; por ejemplo 1970 o el año 0).
  - Los adelantos menores solo se marcan (`published_ahead`).
- **Otras correcciones (`gdelt-news-v2`; la v1 se conserva):**
  - columna `first_seen_at` (= `first_available_at` en el histórico, CLAUDE.md §7);
  - comprobación de que cada fichero del manifiesto pertenece a su día y no está duplicado;
  - hash del código calculado al inicio;
  - test de propiedades y de determinismo.
  - Los ficheros mensuales se parten por `source_file_ts`: para seleccionar por disponibilidad hay que filtrar por `first_available_at`.

## ADR-027 — G1 aprobada y captura en vivo en bucle largo (usuario, 2026-10-16)
- **G1 aprobada** por el usuario sobre `reports/G1_cierre.md`. Se pasa a F3: noticias → eventos.
- **Captura en vivo:** el cron de GitHub de cada 5 min solo se ejecutó 7 veces el 2026-10-16, cada 3–4 h. Hubo saltos de ficheros GDELT de hasta 4,5 h.
  - El usuario elige un **bucle largo**: cada ejecución del workflow dura unas 5,5 h y comprueba cada 5 min, guardando y subiendo las observaciones en cada vuelta.
  - Un cron horario la relanza y la concurrencia (`cancel-in-progress: false`) encadena las ejecuciones sin solaparlas.
  - Es gratis en un repo público. Los límites de GitHub son 6 h por job y 72 h de cola.
- **Riesgo:** en F1 el cron no se disparó con un workflow subido por la app hasta que el usuario hizo un commit del fichero. Si tras el cambio no hay ejecuciones programadas, el usuario debe volver a guardar el workflow en GitHub. Mientras tanto se lanza una ejecución manual.

## ADR-028 — Muestra auditada de G2: Claude propone y el usuario revisa (usuario, 2026-10-16)
- **Decisión:** para los 200–300 titulares del piloto, Claude propone la etiqueta y el usuario la confirma o corrige en una página sencilla. Las etiquetas finales son del usuario.
- **Riesgo aceptado:** sesgo de anclaje hacia la propuesta.
- **Mitigación:**
  - la propuesta de Claude se hace leyendo cada titular, independiente de las reglas que se evalúan;
  - se guardan por separado la propuesta y la etiqueta final;
  - se reporta el % de correcciones.

## ADR-029 — Línea base de eventos F3: parámetros técnicos (2026-10-16)
- **Contexto:** F3, fase 1 del §6.3 (reglas y diccionarios). Estos parámetros son de diseño técnico; no son horizontes, etiquetas, costes ni timestamps. Se fijan aquí y se revisan con la muestra auditada de G2. Cualquier cambio es una versión nueva.
- **Deduplicación online:**
  - URL canónica, sin límite de tiempo;
  - mismo medio y título, o re-publicación del mismo título en otro medio: huecos ≤ 48 h.
- **Eventos:** racha de artículos elegibles con la misma (familia, país principal de GDELT), que se cierra tras 24 h sin artículos.
  - Elegibles: relevancia `direct`, no duplicados y sin `exclude_reason`.
  - Los duplicados heredan el evento.
  - Un cambio de mecanismo dentro de un evento se marca (`mechanism_differs`).
- **Reglas `rules-v1`** (`config/events_rules.yaml`):
  - el título decide la relevancia;
  - los temas u organizaciones solo deciden "directo" si no hay título (2018).
  - Un ensayo sobre un mes real destapó falsos positivos ("crude bomb") que ya están corregidos.
- **Limitación:** la clave (familia, país) es gruesa. Puede juntar acontecimientos distintos del mismo país y familia, o separar uno con varios países. La muestra G2 lo medirá.

## ADR-030 — Revisión adversarial de F3: `rules-v2` y `events-v2` (2026-10-17)
- **Revisión:** sin fugas de información. Hubo 3 hallazgos altos que invalidaban `events-v1`, que no llegó a publicarse: la ejecución se cortó.
- **Cambios:**
  1. **Eventos interminables:**
     - las familias `other`, `market_report` y `opinion` y el país `XX` ya no abren ni alimentan eventos;
     - un evento dura como máximo 72 h desde su primer artículo.
  2. **Sin títulos de 2018-01 a 2019-08** (no solo 2018): si falta el título, las reglas usan el titular derivado del slug de la URL (`title_source = url_slug`). Si tampoco hay slug, deciden los temas.
  3. **País principal:** la moda de los países de las localizaciones V1 (antes era la primera, que no está ordenada por relevancia). Los empates se resuelven por orden alfabético.
  4. **Duplicados en cadena:** `dup_of` apunta siempre al original raíz.
  5. **Misma URL con titular distinto:** ya no se colapsa. Es una actualización que puede cambiar el hecho (CLAUDE.md §7).
  6. **Reglas `rules-v2`:**
     - "price war" y "trade war" fuera de conflicto;
     - "flood" del mercado fuera de clima;
     - titulares "Oil rises/falls…" pasan a directos;
     - "Brent" y "barrels" solo en contexto petrolero;
     - inventarios solo de crudo o productos (no "stocks rise" de bolsa);
     - spam de informes de mercado → no relevante.
  7. Las reglas se aplican al título limpio: entidades HTML decodificadas y sin sufijo del medio.
  8. `mechanism_differs` solo se marca entre mecanismos no ambiguos.
  9. Detalles:
     - tracking de URL anclado a `?`/`&`;
     - medio nulo → dominio de la URL.
  10. **Matiz de la ventana de 48 h:** se mide entre artículos encadenados, sin límite de duración total. Aceptado: son sobre todo titulares repetitivos.
- **Riesgo aceptado:** `hash()` de polars solo es estable dentro de una misma versión de polars. Las claves no se persisten.

## ADR-031 — Etiquetas de G2: el usuario acepta las propuestas de Claude sin revisarlas una a una (usuario, 2026-10-17)
- **Contexto:** ADR-028 preveía que el usuario confirmara o corrigiera cada titular. El usuario ha indicado que da por correctas las propuestas de Claude y que se siga.
- **Decisión:** las etiquetas finales de la muestra G2 son las propuestas de Claude, sin cambios (`reports/f3/g2_labels.csv`, sin titulares).
- **Consecuencias:**
  - la auditoría no es independiente ni humana en sentido estricto: mide el acuerdo entre las reglas y un segundo etiquetador (Claude), que leyó cada titular sin mirar las reglas;
  - la consistencia intra-anotador no se puede medir: las 26 repeticiones copian la etiqueta del original;
  - el % de correcciones del usuario es 0 por construcción, no por evidencia.
- **Mitigación propuesta:** que el usuario revise a mano una submuestra pequeña antes de aprobar las reglas finales (queda como opción en el informe G2).

## ADR-032 — Puerta G2: simplificar la ontología y volver a medir (usuario, 2026-10-17)
- **Contexto:** `reports/G2_eventos.md`. Las reglas `rules-v2` aciertan bien en 5 familias y mal en el resto. La dirección (mecanismo) acierta en 17 de 50 casos.
- **Decisión (opción recomendada, elegida por el usuario):**
  1. **Solo 5 familias generan eventos:** `opec_policy`, `eia_inventories`, `sanctions_geopolitics`, `weather` y `conflict_attack`. Las demás (`company`, `demand_macro`, `logistics`, `rates_usd`, `supply_disruption`, además de `other`, `market_report` y `opinion`) siguen anotadas por artículo, pero no abren ni alimentan eventos (`events-v3`).
  2. **El mecanismo de las reglas no se usa como señal de dirección** en features ni en modelos hasta tener un extractor medido mejor. Se conserva en la capa solo como información.
  3. **Las reglas no cambian** (`rules-v2`): no se ajustan con la muestra de G2, para no medir de forma optimista.
  4. **Segunda medición:** una muestra nueva de 150 titulares (30 por familia, mitad con título real y mitad con titular de la URL), sin artículos de la primera muestra. La etiqueta Claude con el mismo protocolo (ADR-031).
- **Consecuencias:**
  - menos eventos y menos categorías para M4;
  - la prueba M4 frente a M3 se hace sobre familias con extracción medida;
  - G2 sigue abierta hasta el informe de la segunda medición.

## ADR-033 — Deduplicación con el titular del slug cuando falta el título (2026-10-17)
- **Hallazgo (segunda muestra de G2):** de 2018-01 a 2019-08 GDELT no trae título, y la deduplicación por título no veía las copias sindicadas.
  - Medido en `events-v2`: en las 5 familias con eventos, 30.115 de 74.721 artículos elegibles con titular de slug son copias del mismo slug (26.463 de ellas en menos de 48 h).
  - Consecuencia: en 2018-2019 se inflaban `event_seq`, `event_sources_so_far` y el número de eventos, frente a 2019-09 en adelante. Es un cambio de régimen artificial en las futuras features.
- **Corrección:** la clave de título usa `coalesce(título, titular del slug)`. Hay un test de regresión (`test_slug_titles_deduplicate_when_title_missing`).
- **Versión:** entra en `events-v3`, que aún no estaba publicada. `events-v2` queda como está y no se usa para features.
- **Proceso:** el test de `events-v2` tenía la versión fija y falló tras el cambio de configuración; ahora lee la versión de la configuración.
