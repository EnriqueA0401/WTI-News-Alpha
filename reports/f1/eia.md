# F1 — Auditoría EIA (API v2 + web WPSR)

Fecha de auditoría: 2026-10-15 (descargas entre 04:57 y 07:34 UTC). Todas las cifras proceden de ficheros en `data/raw/eia/` registrados en `manifests/eia.jsonl` (135 ficheros, 18 MB, checksums verificados con `RawStore.verify_all`). La clave API no aparece en ningún fichero raw, manifiesto ni script (comprobado con grep). La API v2 no devuelve la clave en el cuerpo (`request.params` no la incluye), así que no hizo falta guardar copias redactadas.

Scripts (`scripts/f1_audit/`): `eia_fetch.py` (descarga al almacén raw), `eia_series_coverage.py`, `eia_archive_dates.py`, `eia_archive_sample.py`, `eia_revision_check.py`, `eia_calendar_crosscheck.py`.

## 1. Comprobaciones realizadas

| # | Comprobación | Resultado |
|---|---|---|
| 1 | Series WPSR clave vía API v2 (ruta `/v2/seriesid/PET.<id>`) | OK: 10 series semanales, sin huecos desde 2015-02-18 |
| 2 | Significado de `period` | Viernes que cierra la semana de datos ("week ending"); 607/607 períodos son viernes |
| 3 | Calendario oficial (`schedule.php`) | Solo excepciones de dic-2024 a nov-2026; no hay años anteriores |
| 4 | Fechas reales de publicación 2015–hoy | **Sí (fechas)**: índice del archivo oficial, 612 publicaciones; cuadran 611/612 con el release 212 de FRED |
| 5 | Horas reales de publicación 2015–hoy | **No verificables** antes de dic-2024: ni el archivo ni los PDF incluyen la hora |
| 6 | Marca de tiempo de publicación/actualización en la API | **No existe** (solo `startPeriod`/`endPeriod` en metadatos) |
| 7 | Revisiones / vintages | La API no tiene vintages. Revisiones medidas: redefinición histórica de existencias comerciales de crudo (~30–34 Mb) en oct-2016; erratas oficiales en 2023 |
| 8 | Plan B diario (RWTC) | Cobertura completa 2015–2026; publicación **semanal** con retraso; fuente Refinitiv (LSEG) |

## 2. Series semanales WPSR (API v2)

Fuente: `data/raw/eia/api/seriesid_*.json`. La ruta `seriesid` ignora `start` y devuelve el histórico completo; por eso `seriesid_WCESTUS1_2015-2026.json` contiene 1982–2026 pese a su nombre (es inmutable y no se renombra). Columnas desde 2015-02-18 (inclusive) hasta el último dato disponible a las 04:57 UTC del 2026-10-15.

| Serie | Descripción (API) | Ruta | Unidades | Inicio histórico | Último `period` | N (≥2015-02-18) | Huecos |
|---|---|---|---|---|---|---|---|
| WCESTUS1 | Existencias de crudo excl. SPR | stoc/wstk | MBBL | 1982-08-20 | 2026-10-02 | 607 | 0 |
| W_EPC0_SAX_YCUOK_MBBL | Existencias de crudo en Cushing | stoc/wstk | MBBL | 2004-04-09 | 2026-10-02 | 607 | 0 |
| WCSSTUS1 | Existencias de crudo en la SPR | stoc/wstk | MBBL | 1982-08-20 | 2026-10-02 | 607 | 0 |
| WGTSTUS1 | Existencias de gasolina total | stoc/wstk | MBBL | 1990-01-05 | 2026-10-02 | 607 | 0 |
| WDISTUS1 | Existencias de destilados | stoc/wstk | MBBL | 1982-08-20 | 2026-10-02 | 607 | 0 |
| WPULEUS3 | Utilización de refinerías | pnp/wiup | % | 1990-11-02 | 2026-10-02 | 607 | 0 |
| WCRFPUS2 | Producción de crudo | (seriesid) | MBBL/D | 1983-01-07 | 2026-10-02 | 607 | 0 |
| WCRIMUS2 | Importaciones de crudo | (seriesid) | MBBL/D | 1990-01-05 | 2026-10-02 | 607 | 0 |
| WCREXUS2 | Exportaciones de crudo | (seriesid) | MBBL/D | 1991-02-08 | 2026-10-02 | 607 | 0 |
| WCRRIUS2 | Input neto de crudo en refinerías | (seriesid) | MBBL/D | 1982-08-20 | 2026-10-02 | 607 | 0 |
| WRPUPUS2 | Producto suministrado total | (seriesid) | MBBL/D | 1990-11-09 | 2026-10-02 | 607 | 0 |

- Sin duplicados ni valores no numéricos en ninguna serie.
- `period` = viernes de cierre de la semana. Lo confirman las 41 páginas de archivo muestreadas que tienen ese campo ("Data for week ending <viernes>"; todas viernes).
- Coherencia point-in-time: a las 04:57 UTC del 2026-10-15 el último `period` era 2026-10-02. Encaja con el calendario: los datos de la semana que cierra el 9-oct salían el jueves 15-oct a las 12:00 ET (`data/raw/eia/api/meta_stoc_wstk.json`, `data/raw/eia/web/wpsr_schedule_2026-10-15.html`).
- Límite de la API: devuelve como máximo 5000 filas por petición. Afecta solo a RWTC (abajo).

## 3. Calendario de publicación (crítico)

### 3.1 Calendario oficial vigente (`data/raw/eia/web/wpsr_schedule_2026-10-15.html`)
- Texto literal: "The standard release time and day of the week will be at 10:30 a.m. eastern time on Wednesday with the following exceptions. All times are eastern."
- Contiene **solo 14 excepciones**, de la semana que cierra el 27-dic-2024 a la del 6-nov-2026. **No hay páginas de años anteriores** en el sitio oficial.

| Semana que cierra | Publicación | Hora ET | Festivo |
|---|---|---|---|
| 2024-12-27 | jue 2025-01-02 | 11:00 a.m. | New Year's Day |
| 2025-01-17 | jue 2025-01-23 | 12:00 p.m. | MLK / Inauguration |
| 2025-02-14 | jue 2025-02-20 | 12:00 p.m. | Presidents' Day |
| 2025-05-23 | jue 2025-05-29 | 12:00 p.m. | Memorial Day |
| 2025-08-29 | jue 2025-09-04 | 12:00 p.m. | Labor Day |
| 2025-10-10 | jue 2025-10-16 | 12:00 p.m. | Columbus Day |
| 2025-11-07 | jue 2025-11-13 | 12:00 p.m. | Veterans Day |
| 2025-12-19 | **lun** 2025-12-29 | **5:00 p.m.** | Christmas |
| 2026-01-16 | jue 2026-01-22 | 12:00 p.m. | MLK |
| 2026-02-13 | jue 2026-02-19 | 12:00 p.m. | Presidents' Day |
| 2026-05-22 | jue 2026-05-28 | 12:00 p.m. | Memorial Day |
| 2026-09-04 | jue 2026-09-10 | 12:00 p.m. | Labor Day |
| 2026-10-09 | jue 2026-10-15 | 12:00 p.m. | Columbus Day |
| 2026-11-06 | jue 2026-11-12 | 12:00 p.m. | Veterans Day |

Las 12 excepciones ya pasadas aparecen como carpeta en el archivo oficial con esa misma fecha (`eia_calendar_crosscheck.py`).

### 3.2 Publicación en dos tiempos (`data/raw/eia/web/wpsr_main_2026-10-15.html`)
- La tabla de productos de la página principal del WPSR tiene la cabecera "Released at | 10:30 a.m. | 1:00 p.m.". Según la estructura del HTML, CSV/XLS salen a las 10:30 y los PDF a las 13:00; esta asignación de columnas es una interpretación.
- Aviso de festivo actual: "will be released on Thursday, October 15, 2026, at 12:00 P.M. and 2:00 P.M. (eastern time) due to the closure of the Federal government on Monday, October 12". `schedule.php` solo dice "12:00 p.m.". Hay que fijar qué hora usar: la primera, la de los datos en CSV.

### 3.3 Reconstrucción histórica 2015–hoy
- **Fechas**: el índice oficial del archivo (`data/raw/eia/web/wpsr_archive_index_2026-10-15.html`) enlaza una carpeta `archive/YYYY/YYYY_MM_DD/` por publicación. Hay 612 publicaciones entre 2015-01-07 y 2026-10-07.
  - Validación de que la carpeta es la fecha de publicación: 41 de las 42 páginas descargadas muestran "Release Date: <misma fecha>" (`data/raw/eia/web/archive/wpsr_*.php.html`). La de 2016-12-07 no tiene ese campo en su HTML.
  - Validación independiente: el release FRED 212 "Spot Prices" tiene 611 fechas en la misma ventana y **coinciden las 611**. La única fecha del archivo que falta en FRED es 2020-01-03 (`data/raw/fred/api/release_212/dates_2015.json`).
- Días de la semana: 523 miércoles, 82 jueves, 6 viernes y 1 lunes. Las 89 no-miércoles por año: 2015: 6, 2016: 7, 2017: 8, 2018: 10, 2019: 8, 2020: 7, 2021: 6, 2022: 7, 2023: 9, 2024: 9, 2025: 8, 2026: 4. Incluye fechas no ligadas a festivos fijos, como jue 2018-12-06 (causa no verificada).
- Muestra de semanas festivas (página del archivo + `highlights.pdf` + `table1.csv`, `eia_archive_sample.py`):

| Año | Publicaciones muestreadas (semana que cierra → publicación) |
|---|---|
| 2015 | 01-02→mié 01-07; 01-16→jue 01-22 (MLK); 02-13→jue 02-19 (Presidents); 05-22→jue 05-28 (Memorial); 07-03→mié 07-08; 09-04→jue 09-10 (Labor); 11-20→mié 11-25 (Thanksgiving); 12-25→mié 12-30 |
| 2019 | 2018-12-28→vie 01-04; 01-18→jue 01-24; 02-15→jue 02-21; 05-24→jue 05-30; 06-28→mié 07-03; 08-30→jue 09-05; 11-22→mié 11-27; 12-20→vie 12-27 |
| 2023 | 2022-12-30→jue 01-05; 01-13→jue 01-19; 02-17→jue 02-23; 05-26→jue 06-01; 06-30→jue 07-06; 09-01→jue 09-07; 11-10→mié 11-15; 11-17→mié 11-22; 12-22→jue 12-28 |
| 2025 | 12-19→lun 12-29; 12-26→mié 12-31 |

- **Horas**: ninguna página del archivo ni ningún `highlights.pdf` muestreado contiene una hora (búsqueda de "10:30", "a.m.", "p.m.", "next release": 0 resultados). **No hay fuente oficial accesible con la hora real de las publicaciones desplazadas de 2015 a nov-2024.** Como ejemplo, la excepción de Año Nuevo 2025 fue a las 11:00 y las demás de 2025–26 a las 12:00, así que la hora de las excepciones varía. Wayback Machine no es accesible desde el entorno (HTTP 403 / ConnectError) y, de todos modos, no es fuente oficial.
- **Semanas sin publicación propia** (sin carpeta en el archivo ni fecha en FRED 212):
  - Semana que cierra el **2022-06-17**: no hay publicación el 2022-06-22.
  - Semana que cierra el **2023-11-03**: no hay publicación el 2023-11-08.

  Los datos de ambas semanas sí están en la API y aparecen como "semana previa" en las publicaciones del 2022-06-29 y del 2023-11-15, con valores idénticos a los de la API. La fecha de su primera publicación **no se pudo verificar**; la única cota segura es "no después de" 2022-06-29 y 2023-11-15. Las páginas de archivo y los PDF de esas fechas no explican el hueco.
- Inconsistencia del archivo: la página `archive/2019/2019_07_03` dice "week ending June 28, 2019", pero su `csv/table1.csv` tiene columnas 6/21/19 y 6/14/19, es decir, el CSV de la semana anterior (`data/raw/eia/web/archive/2019_07_03/table1.csv`). Los ficheros del archivo no son fiables al 100 %.

**Conclusión sobre el calendario:**
- Se puede construir un calendario inmutable de **fechas** de publicación 2015–hoy, verificado contra una segunda fuente (FRED 212). Tiene 2 huecos: las semanas que cierran el 2022-06-17 y el 2023-11-03.
- La **hora** solo está verificada:
  - para la regla estándar vigente (10:30 ET, miércoles);
  - para las 14 excepciones de dic-2024 a nov-2026.
- Para las 77 publicaciones no-miércoles de 2015-01 a 2024-12-27 la hora es **"hora no verificada"**. Lo mismo vale para posibles cambios de hora en miércoles de semanas festivas, como Thanksgiving. Propuesta: tomar como `available_at` conservador el final de la sesión regular de ese día, o excluir esas semanas de las features con hora. Requiere decisión del usuario (§4).
- Tampoco está verificado que la regla de las 10:30 rigiera en todo 2015–2024. Es lo que dice la página vigente, pero no hay copia histórica oficial de esa página.

## 4. Revisiones

- **No hay vintages ni mecanismo as-of** en la API v2. Cada respuesta solo trae el valor vigente, sin fecha de actualización.
- Comparación entre lo impreso en `table1.csv` de 42 publicaciones archivadas y la API actual (`eia_revision_check.py`). Las 3 columnas son: valor en su primera publicación, semana previa impresa y hace un año impreso.
  - **WCESTUS1 (crudo comercial excl. SPR): redefinición histórica.** En las 10 publicaciones muestreadas entre 2015-01-07 y 2016-10-05, la API actual es entre 30.632 y 34.128 MBBL menor que lo impreso (en las 3 columnas). Ejemplos: 2015-02-13 impreso 425.644, API 391.516; 2016-09-30 impreso 499.740 (en la publicación del 2016-10-05), API 469.108. El cambio entra en la publicación del **2016-10-13**: ahí la semana 2016-09-30 aparece como 469.100, que ya coincide con la API. Desde entonces solo hay diferencias de redondeo. La nota explicativa no se encontró en el `highlights.pdf` de esa fecha, así que la causa no está verificada. **Consecuencia**: el nivel de existencias de 2015 a oct-2016 en la API **no es el que vio el mercado** (~31–34 Mb más bajo). En la muestra, la variación semanal (actual − previa) difiere entre 0 y 165 MBBL entre lo impreso y la API (2015-09-10: impreso +2.570, API +2.405).
  - WCRFPUS2, WCRIMUS2, WCREXUS2: 0 diferencias en 42 publicaciones × 3 columnas.
  - WCSSTUS1, WGTSTUS1, WDISTUS1: diferencias ≤ 39 MBBL, solo en los 3 CSV que publican 1 decimal en millones (2016-10-13, 2016-10-19, 2025-12-29). Son compatibles con redondeo, no con revisión.
- **Erratas oficiales** (`data/raw/eia/web/wpsr_appendix_e_errata_2026-10-15.pdf`, "Errata as of December 22, 2023"):
  - Las existencias de propano del PADD 2 se publicaron sobreestimadas del 2023-11-10 al 2023-12-15; en EE. UU. la diferencia llega a 5,339 Mb.
  - El producto suministrado de propano se corrige hasta +519 kb/d, lo que afecta a "total petroleum" (WRPUPUS2).
  - Cita: "We will not republish past editions of the WPSR". Los datos finales se publican en el PSM.
- Los informes indican "Data for the week ending … reflect benchmarking to the … Petroleum Supply Monthly". Es un reajuste periódico que mueve los niveles.
- **Etiqueta: "riesgo de revisión"** para todas las series WPSR leídas de la API, especialmente WCESTUS1 2015–2016 y las de propano/producto suministrado de nov–dic 2023. Para point-in-time real hay que reconstruir los valores de primera publicación desde los `table1.csv`/`table4.csv` del archivo (unas 612 publicaciones × 1–2 CSV de ~6 KB; ≈ 10 MB, ~25 min a 1 req/s).

## 5. Plan B diario: RWTC (WTI spot Cushing, **no es el futuro CL**)

- `data/raw/eia/api/seriesid_RWTC.D_full.json`:
  - Total en la API: 10.264 filas; la respuesta trae solo las 5000 más recientes (2006-11-08 a 2026-10-06). Cubre 2015 entero.
  - 2920 días desde 2015-02-18. Unidades: $/BBL. Incluye -36,98 el 2020-04-20.
  - 115 días laborables (lun–vie) sin dato. La mayoría son festivos de EE. UU.; 5 no son festivos federales evidentes: 2022-10-25, 2022-12-13, 2023-03-07, 2024-02-06 y 2024-04-16 (causa no verificada).
- **Semántica temporal**:
  - Es el precio de cierre spot diario (página de definiciones: "daily closing spot prices"; fuente "Refinitiv, an LSEG business", `data/raw/eia/web/dnav_pet_pri_spt_tbldef2_2026-10-15.html`).
  - **Se publica semanalmente**: "Release Date: 10/7/2026 Next Release Date: 10/15/2026" (`data/raw/eia/web/dnav_pet_pri_spt_s1_d_2026-10-15.html`). El 2026-10-15 el último dato era el del 2026-10-06.
  - Por tanto, el valor del día d **no está disponible en d**, sino en la siguiente publicación semanal. Retraso medido vía ALFRED (DCOILWTICO): mediana de 6 días, máximo de 15 (ver `fred.md`). La hora de publicación no está verificada.
  - Licencia: el dato es de Refinitiv/LSEG redistribuido por EIA. Hay que revisar los términos de reutilización antes de usarlo como serie principal.
- Coincide al 100 % con FRED DCOILWTICO actual en 2931 fechas comunes.

## 6. Riesgos

1. Horas de publicación sin verificar en las semanas desplazadas de 2015 a 2024-12-27 (77 casos no-miércoles). Si se asume 10:30 cuando fue más tarde, hay leakage.
2. Dos semanas (2022-06-17 y 2023-11-03) con fecha de primera publicación desconocida.
3. La API sirve datos revisados o redefinidos (WCESTUS1 2015–2016 con ~31–34 Mb de diferencia). Sin reconstrucción desde el archivo, los niveles de existencias no son point-in-time.
4. El archivo oficial tiene al menos un CSV mal asociado (2019-07-03). Cada CSV debe validarse contra su "week ending".
5. RWTC: publicación semanal, licencia de terceros, 5 huecos sin explicar y límite de 5000 filas en la API.
6. Doble horario (10:30/13:00 y 12:00/14:00 en festivos): falta decidir a qué hora se considera disponible cada formato.

## 7. Recomendación

- Usar el **índice del archivo WPSR** como registro inmutable de fechas de publicación 2015–hoy, contrastado con FRED 212. Hora: 10:30 ET los miércoles estándar; la hora oficial para las excepciones de dic-2024 en adelante; para las excepciones 2015–nov-2024, **"hora no verificada"** con política conservadora. Pendiente de decisión.
- Construir los **valores de primera publicación** desde los CSV archivados (table1/table4) en lugar de la API, al menos para WCESTUS1, Cushing, gasolina, destilados, producción, importaciones, exportaciones y utilización. La API queda para valores "finales" y control de calidad.
- Las sorpresas EIA se calculan con valores de primera publicación (A12).
- RWTC/DCOILWTICO como Plan B solo con `available_at` = fecha de la publicación semanal, nunca el día del precio.
- Arrancar en F2 un monitor prospectivo (sondeo de `endPeriod` de la API y de `ir.eia.gov/wpsr`) para medir la hora real de publicación desde ya.

## 8. Preguntas abiertas (decisión del usuario)

1. Política para las publicaciones desplazadas 2015–nov-2024 sin hora: (a) conservadora, disponible en el cierre de sesión del día; (b) excluir esas semanas; (c) buscar otra fuente fechada (por ejemplo, notas de prensa con hora o noticias GDELT del propio release), con verificación posterior.
2. ¿Qué hora usar con doble horario: la de CSV (10:30/12:00) o la de PDF (13:00/14:00)? Recomendación: la de CSV, porque los datos ya son públicos.
3. ¿Se aprueba descargar el archivo completo de `table1.csv`/`table4.csv` (≈ 1200 ficheros, ~10 MB) para reconstruir los valores de primera publicación?
4. ¿Se excluyen o se marcan las semanas 2022-06-17 y 2023-11-03?
