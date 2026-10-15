# F1 — Auditoría de fuente: NOAA NCEI Storm Events Database

Fecha de auditoría: 2026-10-15 (UTC). Ventana de estudio: 2015-02-18 → presente.
Raw: `data/raw/noaa/**` (14 ficheros, 64,2 MB, manifiesto `manifests/noaa.jsonl`, checksums verificados).
Scripts: `scripts/f1_audit/noaa_{fetch,listing,analyze}.py` (ruff OK).

## 1. Qué se comprobó

| # | Comprobación | Resultado |
|---|---|---|
| 1 | Listado `csvfiles/` y sellos `cYYYYMMDD` | Solo existe la versión vigente de cada año; casi todos los años se regeneraron el 2026-03-23 |
| 2 | Timestamp de publicación por evento | **No existe** en los ficheros públicos (`details-ftp`) |
| 3 | Retraso de publicación | ~2,5–3,5 meses entre el fin del mes y su aparición (2 observaciones) |
| 4 | Revisiones posteriores | Sí: eventos añadidos y modificados años después |
| 5 | Relevancia petrolera (2017, 2020, 2021) | Miles de eventos al año en TX/LA/OK/NM/ND/Golfo; huracanes y heladas bien representados |
| 6 | Alternativa en tiempo real | Existe el archivo de NHC (solo se comprobó el índice) |

## 2. Ficheros y sellos de creación

Fuente: `noaa/stormevents/csvfiles_listing.html`.

| Fichero details (año de datos) | Sello `c` | Last-modified | Tamaño (B) |
|---|---|---|---|
| d2014–d2016, d2018–d2021, d2023 | c20260323 | 2026-03-23 13:10 (d2020: 13:24) | 9–13 M |
| d2017 | c20260519 | 2026-05-19 11:22 | 9.340.854 |
| d2022 | c20260625 | 2026-06-25 14:39 | 11.817.849 |
| d2024 | c20260728 | 2026-07-27 21:11 | 12.693.243 |
| d2025 | c20260819 | 2026-08-19 18:22 | 12.585.209 |
| d2026 | c20260918 | 2026-09-18 10:57 | 7.490.892 |

- README (`noaa/stormevents/README.txt`): "We will update the current yearly file each month… updates and corrections may be made to previous years. A change of this date value will indicate that data changes have been made."
- **Los sellos `c` no sirven para fechar la primera publicación.** Al regenerar un fichero, la versión anterior desaparece del listado. Solo quedan 5 ficheros antiguos en `archive/` (formato "database": d2006, d2018–d2021, creados en jun-2021; `noaa/stormevents/archive_listing.html`). Wayback Machine no es accesible desde este entorno, así que no se pudo reconstruir el historial de sellos.

## 3. Hora de publicación por evento

- Campos documentados (`noaa/stormevents/Storm-Data-Bulk-csv-Format.pdf`): solo `BEGIN_*`/`END_*` (hora **local** con `CZ_TIMEZONE`). **No hay campo de publicación o ingesta** en `details-ftp` (columnas temporales en d2026: BEGIN_TIME, END_TIME, BEGIN_DATE_TIME, CZ_TIMEZONE, END_DATE_TIME).
- El formato "database" de `archive/` sí incluye `LAST_MOD`, `LAST_CERT` y `ADDCORR_FLG/DATE`. Son fechas de la última modificación o certificación interna, no de publicación, y solo existen para esos 5 ficheros.

## 4. Retraso de publicación y revisiones (medido)

| Medida | Valor | Fuente raw |
|---|---|---|
| Último mes presente en d2026, sello 2026-09-18 | 2026-06 (la fecha de fin de evento más tardía también es de 2026-06) → **≥ 80 días** desde el fin del mes | `csvfiles/…d2026_c20260918.csv.gz` |
| Último mes presente en d2021, sello 2021-06-07 | 2021-02 (solo ene–feb, 10.709 eventos) → **≥ 99 días** | `archive/…d2021_c20210607.csv.gz` |
| `LAST_CERT − END_DATE_TIME`, eventos de 2021 (n=10.709) | mín. 13,3 d · p10 52,2 · mediana 72,1 · p90 86,2 · máx. 122,2 | ídem |
| `LAST_CERT − END_DATE_TIME`, eventos de 2020 (n=60.714) | mín. 0,9 d · p10 49,6 · mediana 74,0 · p90 121,1 · máx. 482,1 | `archive/…d2020_c20210604.csv.gz` |
| Revisión 2020: versión jun-2021 → versión vigente (c20260323) | 60.714 → 61.281 eventos; **+570** añadidos (125 de jul-2020 y 144 de ago-2020), 3 ausentes; 314 eventos comunes cambian algún campo (EVENT_TYPE 63, BEGIN 218, END 223, DAMAGE_PROPERTY 23, DEATHS_DIRECT 4) | ambos d2020 |
| Revisión ene–feb 2021: versión 2021-06-07 → versión vigente | 10.709 → 11.671; **+962** (851 de feb-2021); 30 eventos cambian | ambos d2021 |
| Eventos invernales o de frío en Texas, feb-2021 | 660 (en jun-2021) → 665 (vigente) | ambos d2021 |

Notas:
- `END_DATE_TIME` está en hora local y la zona de `LAST_CERT` no está documentada, así que la precisión es de ~1 día.
- `LAST_CERT` es la última certificación, no la primera.
- La publicación en el FTP es posterior a la certificación: el fichero de 2021-06-07 contenía certificaciones hasta el 2021-05-14.

## 5. Relevancia: eventos en regiones petroleras

Regiones: TX, LA, OK, NM, ND y zonas marinas del Golfo. En los datos, `STATE = "GULF OF MEXICO"`; `CZ_TYPE` solo toma los valores C y Z, nunca M. Ficheros vigentes d2017 (c20260519), d2020 y d2021 (c20260323).

| Año | Eventos totales | En regiones petroleras | TX | OK | LA | NM | ND | Golfo (marino) |
|---|---|---|---|---|---|---|---|---|
| 2017 | 57.041 | 8.789 | 3.621 | 1.933 | 825 | 761 | 720 | 929 |
| 2020 | 61.281 | 9.516 | 4.128 | 1.630 | 875 | 763 | 927 | 1.193 |
| 2021 | 61.389 | 10.260 | 4.623 | 1.324 | 784 | 1.300 | 1.196 | 1.033 |

Tipos relevantes (TX / LA / Golfo):

| Tipo | 2017 | 2020 | 2021 |
|---|---|---|---|
| Hurricane (Typhoon) | 9 / 0 / – | 15 / 37 / – | 1 / 30 / – |
| Tropical Storm | 40 / 22 / – | 49 / 84 / – | 26 / 17 / – |
| Storm Surge/Tide | 7 / 22 / – | 50 / 38 / – | 18 / 19 / – |
| Marine Tropical Storm (Golfo) | 40 | 93 | 71 |
| Marine Hurricane/Typhoon (Golfo) | 15 | 27 | 12 |
| Winter Storm (TX / LA / OK) | 4 / 0 / 8 | 57 / 0 / 40 | **270 / 75 / 48** |
| Extreme Cold/Wind Chill (TX / ND) | 1 / 51 | 0 / 28 | **127 / 85** |
| Ice Storm (TX / OK) | 7 / 22 | 23 / 41 | 59 / 0 |

- **Tropicales en TX:** 50 en ago-2017.
- **Tropicales en LA:** 44 en ago-2020, 93 en oct-2020 y 53 en ago-2021.
- **Invernales o de frío en TX:** 665 en feb-2021.
- **Por nombre en las narrativas** (búsqueda aproximada): "Harvey" aparece en 319 eventos de TX/LA/Golfo (ago–nov 2017), "Laura" en 260 (ago–oct 2020) y "Nicholas" en 84 (sep 2021). Otros nombres dan falsos positivos (p. ej. "Delta" o "Ida" como palabras comunes) y no se usan.

## 6. Conclusión point-in-time

- **NOAA Storm Events no puede ser point-in-time.** No tiene timestamp de publicación por evento. Los datos aparecen ≥ 80–99 días después del mes. Se revisan años después (cientos de eventos añadidos y cambios en tipo, fecha y daños). No se conservan las versiones anteriores (salvo 5 ficheros de 2021).
- **Conforme a PROTOCOL A11:** solo sirve como **contexto retrospectivo** (etiquetado y validación de eventos meteorológicos ya identificados por noticias, estudio de eventos ex post) y debe marcarse como "sin disponibilidad real". **Excluir como feature.**

## 7. Alternativa en tiempo real (solo mencionada)

- El archivo de NHC (`https://www.nhc.noaa.gov/archive/`) responde y lista directorios por año desde 1998 (`noaa/nhc/archive_index.html`). Los avisos (advisories) de NHC llevan hora de emisión y serían candidatos point-in-time para huracanes en el Golfo. **No se descargó ni se auditó su contenido.**
- Para olas de frío, habría que buscar avisos o alertas de NWS archivados (p. ej. IEM/NWS) en otra auditoría.
- Alternativa práctica: capturar el meteo a través de las **noticias** (GDELT), que ya tienen `first_available_at`.

## 8. Riesgos

1. Usar Storm Events como feature introduciría *look-ahead* severo (meses) y sesgo de revisión.
2. No se pudo reconstruir el historial de primeras apariciones: el retraso se basa solo en 2 instantáneas y en la certificación de 2020–2021.
3. Los nombres de estado del Golfo podrían cambiar en datos futuros (el script contempla también "GULF OF AMERICA"; no aparece en los ficheros auditados).

## 9. Recomendación y preguntas abiertas

- **Recomendación:** excluir NOAA Storm Events de las features. Mantenerlo opcionalmente como fuente de contexto o validación ex post, sin entrar en el pipeline PIT.
- **P1:** ¿se abre una auditoría específica de los avisos de NHC (y de las alertas de NWS por frío) como fuente meteorológica point-in-time, o se delega el meteo en las noticias?
- **P2:** ¿interesa conservar Storm Events para etiquetar o validar la taxonomía de eventos de F3 (solo ex post)?
