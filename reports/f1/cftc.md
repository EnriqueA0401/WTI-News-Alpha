# F1 — Auditoría de fuente: CFTC Commitments of Traders (COT)

Fecha de auditoría: 2026-10-15 (UTC). Ventana de estudio: 2015-02-18 → presente.
Raw: `data/raw/cftc/**` (43 ficheros, 59,4 MB, manifiesto `manifests/cftc.jsonl`, checksums verificados con `RawStore.verify_all`).
Scripts: `scripts/f1_audit/cftc_{fetch,htmltext,analyze,api_compare,revision_check,calendar,socrata_times}.py` (ruff OK).

## 1. Qué se comprobó

| # | Comprobación | Resultado |
|---|---|---|
| 1 | Informe y código de mercado WTI | **Disaggregated**, código `067651` (NYMEX). Disponible Futures Only y Futures+Options Combined |
| 2 | Cambios de nombre | `CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE` hasta as-of 2022-02-01; `WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE` desde 2022-02-08. El código no cambia |
| 3 | Cobertura 2015–2026 | 607 fechas as-of en la ventana (2015-02-24 → 2026-10-06), sin semanas perdidas ni duplicados |
| 4 | Coherencia zip anual ↔ API Socrata | 614/614 fechas idénticas; 73.066 celdas comparadas, sin diferencias numéricas (solo distinta codificación de `cftc_region_code` y un "-0" frente a "0") |
| 5 | Calendario real de publicación | Regla oficial contrastada; hay sellos por fila en la API desde oct-2022; para 2015–2022 solo hay regla + anuncios (ver §3) |
| 6 | Revisiones | La CFTC corrige sin publicar versiones (vintages); para 067651 no se detecta ninguna revisión en la ventana (ver §4) |

## 2. Dataset, código y cobertura

| Elemento | Valor | Fuente raw |
|---|---|---|
| Futures Only (zip anual) | `fut_disagg_txt_YYYY.zip` (2015–2026), un fichero `f_year.txt`, 191 columnas | `cftc/history/fut_disagg_txt_*.zip` |
| Futures+Options (zip anual) | `com_disagg_txt_YYYY.zip`, `c_year.txt`, 191 columnas | `cftc/history/com_disagg_txt_*.zip` |
| API Socrata `72hh-3qpy` | "Disaggregated - Futures Only" (194 columnas) | `cftc/socrata/views_72hh-3qpy.json` |
| API Socrata `kh3c-gbw2` | "Disaggregated - Combined" (futures + options) | `cftc/socrata/views_kh3c-gbw2.json` |
| Id de fila en la API | `YYMMDD` + código + `F`/`C` (p. ej. `261006067651F`) | `cftc/socrata/72hh-3qpy_067651_2015plus.json` |
| Renombrado a WTI-PHYSICAL | Anuncio del 2022-02-11 (lista de nombres nuevos; "contract market codes … are unaffected") | `cftc/pages/historical_special_announcements.htm` |
| Open interest 067651 (FutOnly) | mín. 1.395.622 / máx. 2.706.454 contratos (2015–2026) | zips `fut_` |
| Open interest 067651 (Combined) | mín. 1.823.563 / máx. 3.785.409 | zips `com_` |

Otros mercados WTI que **no** deben confundirse con 067651 (zip 2025–2026): `06765A` WTI FINANCIAL CRUDE OIL (NYMEX), `067411` CRUDE OIL, LIGHT SWEET-WTI (ICE Futures Europe), `06741Q` WTI CRUDE OIL 1ST LINE (ICE), varios diferenciales.

**Semántica "as of":** `Report_Date_as_YYYY-MM-DD` es la fecha de las posiciones (cierre del martes), **no** la de publicación ("Dates indicate the report date and not the release date", `cftc/pages/historical_viewable.htm`). Hay 6 as-of en lunes, todos cuando el martes era festivo: 2017-07-03, 2018-12-24, 2018-12-31, 2020-12-21, 2023-07-03, 2025-11-10 (la de 2020 por el festivo extra del 24-dic, anuncio del 2020-12-28).

## 3. Calendario de publicación (crítico)

**Regla oficial.** "released every Friday at 3:30 p.m. Eastern time" y "the third business day after the 'as of' date" (`cftc/pages/about_cot.htm`); "Federal holidays may delay release by one or two days" (`cftc/schedule/release_schedule_index.htm`). Implementada en `cftc_calendar.py` como: **3.er día hábil federal tras el as-of, 15:30 ET** (festivos por regla estatutaria + cierres ad hoc anunciados por la CFTC: 2018-12-05, 2020-12-24, 2025-01-09).

**Contraste de la regla con fechas oficiales:**

| Evidencia oficial | Casos | Coincide con la regla |
|---|---|---|
| Calendario 2026 (tentativo), fechas ya alcanzadas por los datos | 41 de 52 | 41/41 |
| Columna "Original Publish Date" de la tabla 2025 (PR 9147-25) | 13 | 13/13 |
| Desplazamientos anunciados (2015-07-06, 2020-12-28, 2021-06-21, 2025-01-13) | 4 | 4/4 |
| Sello `:created_at` de la API (desde as-of 2022-10-04), mismo día que la regla | 190 de 210 | 190 (los 20 restantes son ION 2023 y cierre 2025, abajo) |

**Hora real (API Socrata, `:created_at` por fila, `cftc/socrata/72hh-3qpy_067651_systemfields.json`):** en 197 cargas del día de la regla, 178 entre 15:30:00 y 15:31 ET, 14 entre 1 y 5 min, 4 con más de 30 min de retraso (máximo: 2025-07-18, 17:34 ET, +124 min) y una antes de las 15:30 (2022-08-05 11:39 ET, en la carga previa al lanzamiento de la API). La última carga (as-of 2026-10-06) fue el viernes 2026-10-09 a las 15:30:07 ET. El metadato `rowsUpdatedAt` del dataset da 2026-10-09 19:30:08 UTC (`views_72hh-3qpy.json`). La API se lanzó en oct-2022 (`about_cot.htm`). Las 9 cargas de ago–sep 2022, entre ellas 2 con 5 días de retraso, son de antes del lanzamiento y no reflejan la publicación real.

**Excepciones verificadas (fecha real de publicación ≠ regla):**

| Episodio | As-of afectados | Publicación real | Evidencia |
|---|---|---|---|
| Publicación prematura e incompleta | 2015-06-30 | Se publicó por error el viernes 2015-07-03; la oficial completa salió el lunes 2015-07-06 a las 15:30 | Anuncio del 2015-07-06 |
| Cierre del gobierno 2018–19 | 2018-12-24 → ~mar-2019 | Último informe publicado el 2018-12-21. El as-of 2018-12-24 salió el **2019-02-01**; después, "uno el martes y otro el viernes" hasta ponerse al día | PR 7864-19 (`cftc/press/7864-19.htm`) |
| Incidente ION (ciberataque) | 2023-01-31, 02-07, 02-14, 02-21, 02-28, 03-07, 03-14 | 2023-02-24, 03-03, 03-08, 03-10, 03-14, 03-16, 03-21 (todas ~15:30 ET) | Anuncios feb–mar 2023, PR 8662-23 y `:created_at` de la API (coinciden 7/7) |
| Cierre del gobierno 2025 | 2025-09-30 … 2025-12-23 (13 semanas) | Del 2025-11-19 al 2025-12-31 (tabla de la PR 9147-25) | PR 9138-25 y 9147-25 + `:created_at` (12/13 coinciden) |
| **Discrepancia** | 2025-12-23 | Tabla oficial: 2025-12-29. API: **2025-12-31 15:30:46 ET** | Usar 2025-12-31 (lo más conservador) y preguntar o verificar |

**¿Se puede construir un calendario verificado para toda la ventana?** **Sí desde oct-2022, y solo parcialmente para 2015-02 → 2022-09.**
- **Oct-2022 → hoy:** verificado por fila con `:created_at` más las tablas oficiales.
- **2015-02 → 2022-07 (389 publicaciones):** no hay sello por fila. La CFTC solo publica el calendario del año en curso (2026). Los calendarios de años pasados no aparecen en cftc.gov (las URL que probé dan 404, no hay sitemap y la búsqueda devuelve 403). Wayback Machine no es accesible desde este entorno (el proxy corta la conexión). Las semanas normales (viernes) dependen de la regla. **23 semanas con desplazamiento por festivo** que predice la regla no tienen confirmación oficial: 2015-11-10/11-24/12-22/12-29, 2016-11-08/11-22, 2017-11-07/11-21, 2018-07-03/11-20/12-04, 2019-07-02/11-26/12-24/12-31, 2020-06-30/11-10/11-24/12-29, 2021-11-09/11-23/12-21/12-28 (as-of; la publicación prevista es el lunes siguiente). La de 2018-12-04 depende además del cierre del 2018-12-05.
- **Hueco principal:** las fechas exactas de la puesta al día de feb–mar 2019. Solo están verificados la primera (2019-02-01) y el patrón martes/viernes.

## 4. Revisiones

- **Declaración oficial:** "When large reporting or classification issues are found, an announcement is made and corrections are published as quickly as possible" (`cftc/pages/disagg_explanatory_notes.htm`). Los anuncios recogen correcciones de otros mercados (p. ej. 2017-04-04, 2018-09-25/26) y, como precedente, una reclasificación del sector energía que reescribió el histórico desde jul-2007 (anuncio del 2008-07-25). **Los zips y la API contienen solo la versión vigente: no hay vintages.**
- **Para 067651 en la ventana:** ningún anuncio de corrección (el único anuncio que la menciona es el renombrado de 2022).
- **Prueba indirecta** (`cftc_revision_check.py`): `Change_in_X(t)` se fija al publicar. Coincide con `X(t)−X(t−1)` del histórico actual en las 613 transiciones de Futures Only para 11 series (OI, Prod/Merc, Swap, MM, Other, NonRept, long/short): **0 discrepancias**. En Combined, todas las diferencias son de ±1 (redondeo de opciones ajustadas por delta).
- **API:** solo 1 fila de 067651 tiene `:updated_at ≠ :created_at` (as-of 2022-08-16, actualizada 4 h después, en la carga previa al lanzamiento).
- Limitación: la prueba no detectaría una revisión que la CFTC hubiera propagado también a las columnas `Change_in_*`.

## 5. Riesgos

1. Fechas de publicación de 2015–2022 derivadas de una regla, no observadas (23 semanas festivas y la puesta al día de 2019 sin confirmar).
2. Una publicación prematura en 2015-07-03 y retrasos puntuales de hasta +124 min respecto a las 15:30 en la API. No se sabe si la web publicó a la hora y solo la API se retrasó.
3. Sin vintages: si hubo revisiones no anunciadas, el histórico actual tendría *look-ahead* (riesgo bajo según §4).
4. La publicación del viernes a las 15:30 ET cae después del settlement de CL (14:30 ET) y antes de la pausa de las 17:00 ET.
5. Discrepancia entre la tabla oficial y la API para el as-of 2025-12-23. **Revisión G0:** la regla no incluye los cierres federales por orden ejecutiva (p. ej., días de Nochebuena o el 26-dic). Si hubo cierres el 24 y 26-dic-2025, la regla daría 2025-12-31, igual que la API. Un posible cierre el 24-dic-2015 movería la publicación del as-of 2015-12-22. Estos errores son de días enteros y en la dirección que filtra datos: hay que añadir los cierres verificados (Federal Register/OPM) o aplicar la regla conservadora R-PIT.

## 6. Recomendación

- Usar **Disaggregated Futures Only, código 067651**, desde los zips anuales (más estables) con contraste contra la API. Combined, solo como variante.
- `available_at` = `:created_at` de la API cuando exista (as-of ≥ 2022-10-04). Si no existe: fecha de la regla o fecha oficial anunciada a las 15:30 ET, **más un margen conservador** (propuesta: activar en la siguiente sesión de CL, domingo 18:00 ET, o como mínimo a las 15:30 ET + 2 h, que cubre el retraso máximo observado). La decisión afecta a timestamps, así que requiere ADR y aprobación (§4).
- Codificar como excepciones fijas: 2015-07-06, el cierre de 2018–19, ION 2023 y el cierre de 2025. Marcar la puesta al día de 2019 como `release_time_unverified` o excluir esas semanas.
- Marcar COT como "sin vintages, revisión no observada" en el diccionario de datos.

## 7. Preguntas abiertas

1. ¿Qué margen sobre las 15:30 ET se aprueba para `available_at` (15:30, +2 h o apertura del domingo)?
2. ¿Se aceptan como suficientes la regla del 3.er día hábil y los anuncios para 2015–2022, o se busca otra vía para los calendarios históricos (p. ej. acceso a Wayback desde otra red, o pedirlos a publicreporting@cftc.gov)?
3. Puesta al día de feb–mar 2019: ¿excluir esas semanas o asignarles una fecha conservadora? Ni el número exacto de semanas ni la fecha final de la puesta al día están verificados.
4. As-of 2025-12-23: ¿se confirma el uso de 2025-12-31?
