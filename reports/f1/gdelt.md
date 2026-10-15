# F1 — Auditoría GDELT 2.0

Fecha: 2026-10-15. Datos crudos en `data/raw/gdelt/` (verificados contra `manifests/gdelt.jsonl`).
Scripts: `scripts/f1_audit/gdelt_*.py`. Todas las cifras salen de esos ficheros.

## 1. Qué se comprobó
| Paso | Fuente | Salida |
|---|---|---|
| Índice maestro completo (1.203.996 líneas, 128,6 MB) | `masterfilelist.txt` | `reports/f1/gdelt_coverage.csv` |
| Muestra estratificada: días 10 de mar/jun/sep/dic de 2015–2026, slots 08:00 y 15:00 UTC, más 8 slots consecutivos el 2024-06-10 14:00–15:45 (304 ficheros, 0,72 GB) | ficheros GKG/Export/Mentions | `gdelt_sample_files.csv`, `gdelt_sample_lags.csv`, `gdelt_sample_export_mentions.csv`, `gdelt_volume_estimates.csv` |
| `Last-Modified` (HEAD) de los 304 ficheros de la muestra | servidor GDELT | `gdelt_lastmodified.csv` |
| Monitor en vivo de `lastupdate.txt` cada 30 s (en curso durante la noche) | servidor GDELT | `data/raw/gdelt_lag_monitor/` |
| Prueba de DOC API | api.gdeltproject.org | ver §6 |

## 2. Cobertura (índice maestro, 2015-02-18 23:00 → 2026-10-15 05:00 UTC)
- Rejilla de 15 min perfecta: 0 timestamps fuera de rejilla y 0 duplicados.
- 61 líneas del índice están truncadas (solo el prefijo de URL); no afectan.
- **Huecos:** 7.291 slots sin Export/Mentions y 7.286 sin GKG (1,78 % del periodo). Las rachas más largas:

| Desde (UTC) | Duración |
|---|---|
| 2025-06-14 18:00 | **416 h (≈ 17 días)** |
| 2015-10-21 04:15 | 41,8 h |
| 2023-03-22 04:45 | 32,2 h |
| 2020-11-13 09:15 | 22,0 h |
| 2020-11-06 11:00 | 20,2 h |

- Huecos por año (GKG): 2015: 200 · 2016: 44 · 2017: 990 · 2018: 992 · 2019: 11 · 2020: 2.585 · 2021: 610 · 2022: 5 · 2023: 165 · 2024: 2 · 2025: 1.681 · 2026: 1.
- **Slots degradados (revisión G0):** 2.859 ficheros GKG pesan < 200 KB (la mediana es de 4–11 MB). 2.565 caen entre mar y nov de 2017 (máximo: 562 en jul-2017) y 83 en nov-2022. La cobertura efectiva de 2017 es mucho peor de lo que dicen sus 990 huecos. Se tratarán como hueco de la fuente (umbral por tamaño o nº de filas, a fijar en F2).
- **Ficheros del índice que dan 404:** 8 de 642 comprobados (2022-11-10/11 y 2023-03-23 13:00). El índice no garantiza que el fichero exista.
- **Consecuencia:** "sin fichero" ≠ "sin noticias". Las features de noticias deben distinguir hueco de la fuente (dato ausente) de ventana sin eventos; los periodos con hueco quedan marcados.

## 3. Volumen y tamaño
- GKG comprimido: 273 GB (2015) … 165–215 GB/año (2021–2025). **Total ≈ 2,9 TB.** Export ≈ 2–5 GB/año; Mentions ≈ 3–9 GB/año.
- Artículos GKG estimados (inglés): de ~92 M (2015) a ~35–48 M/año (2023–2026).
- Artículos con tema `ECON_OILPRICE` (estimación; IC 95 % por t con n = 8 ficheros/año; los slots 08 y 15 UTC son horas activas, así que **sesgo al alza**):

| Año | k art./año | ± IC95 |
|---|---|---|
| 2015 | 1.797 | 439 |
| 2016 | 1.075 | 655 |
| 2017 | 404 | 324 |
| 2018 | 758 | 432 |
| 2019 | 407 | 119 |
| 2020 | 1.290 | 1.145 |
| 2021 | 362 | 163 |
| 2022 | 499 | 466 |
| 2023 | 201 | 167 |
| 2024 | 299 | 116 |
| 2025 | 288 | 99 |
| 2026 (parcial) | 1.250 | 719 |

  El criterio amplio (`ENV_OIL` o `ECON_OILPRICE`) da 0,8–3,7 M/año. Son artículos etiquetados, no eventos únicos ni relevancia WTI auditada: F3 lo depurará. La muestra (n = 8 por año) solo da el orden de magnitud.

## 4. Timestamps (crítico para `first_available_at`)
1. **GKG:** el campo `DATE` coincide con el timestamp del fichero en el 100 % de filas. No hay otra hora "vista por GDELT" por artículo.
2. **Export/Mentions:** `DATEADDED` y `MentionTimeDate` = timestamp del fichero en el 100 % (101 + 101 ficheros).
3. **Hora real de publicación del fichero** (corregido tras la revisión G0; `gdelt_lastmodified.csv`, `gdelt_lastmodified_edge.csv`):
   - **Slots normales:** `Last-Modified` de los GKG de la muestra fija (n = 101) cae entre 12,0 y 2,1 min antes del nombre. Con los tres tipos (n = 303), el máximo es +0,5 min: 3 casos positivos, todos del slot degradado 2017-09-10 08:00.
   - **Ficheros degradados (< 200 KB; 145 comprobados con HEAD):** 143 se subieron después del nombre, como mucho **+2,0 min**.
   - **Primer fichero tras un hueco de ≥ 4 slots (489 comprobados):** 15 llegaron tarde, 12 más de +5 min y 5 más de +15 min. El peor, **+79,7 min** (2020-11-18 00:15). Tras una caída, GDELT sube con retraso.
   - **Monitor en vivo:** `…050000` publicado a las 04:53:30 UTC y `…051500` a las 05:05:08 (−9,9 min). Solo 2–3 medidas: el contenedor se reinició a las ~05:05 y el monitor se relanzó a las 07:25.
   - **Conclusión:** el timestamp nominal **no siempre** es conservador. Regla propuesta (ADR-012 revisado): `first_available_at = max(nominal + 15 min, Last-Modified + 5 min)`. Last-Modified se registra en cada descarga de F2.
4. **Publicación del artículo** (`PAGE_PRECISEPUBTIMESTAMP` en `V2EXTRASXML`): presente en 0 % (2015), 27 % (2016) y 37–57 % (2017–2026).
   - Retraso fichero − publicación: mediana 90–128 min; p75 207–387 min; p95 0,49–1,64 días.
   - **4,4–6,9 % con publicación posterior al fichero** (p1 entre −98 y −380 min). Son timestamps incoherentes (zona horaria mal declarada, probablemente): se marcan y excluyen (CLAUDE.md §7).
   - **2,8–7,1 % publicados más de 1 día antes** de que GDELT los viera (re-rastreos). Riesgo de "falsa novedad": no tratarlos como noticia nueva.
5. **Títulos** (`PAGE_TITLE`): 0 % en 2015–2018, 27 % en 2019 y 100 % desde 2020. Antes de 2019 la relevancia solo puede venir de temas y entidades GKG.

## 5. Duplicados
- Dentro de un fichero: 9 (2015), 5 (2016), 1 (2017) y 0 después.
- 8 actualizaciones consecutivas (2024-06-10, 14.724 filas): 0 URL repetidas entre ficheros. GDELT no repite la misma URL en lotes contiguos.
- En toda la muestra, 22 URL aparecen en ficheros de días distintos (re-rastreos). Sindicación y casi-duplicados entre medios no medidos aún: es F3 (RapidFuzz/título).

## 6. DOC API
Responde 429 casi siempre desde este entorno ("Please limit requests to one every 5 seconds"); una sola petición devolvió 200 con resultado vacío. No es fiable desde aquí ni necesaria: el GKG trae títulos desde 2019.

## 7. Coste de procesar el histórico (medido)
- Descarga en paralelo (8 conexiones): **≈ 94 MB/s** (48 ficheros GKG, 268 MB en 2,9 s).
- Parseo + filtro de temas con Polars: **≈ 0,17 s por fichero GKG** (1 núcleo; la máquina tiene 4).
- Estimación: 1 año (≈ 200 GB, ≈ 35.000 ficheros) ≈ 35–60 min de descarga + ≈ 0,5 h de CPU en 4 núcleos. Histórico completo (≈ 2,9 TB) ≈ 9–10 h. Ambas superan los topes de CLAUDE.md §4 (> 2 GB o > 2 h): **requieren aprobación**.
- Disco: se filtraría en streaming, guardando solo las filas relevantes más su manifiesto (sha del fichero de origen = md5 del índice + versión del filtro), sin conservar los zip completos.

## 8. Riesgos
- Huecos largos (17 días en 2025) y años con muchos huecos (2017, 2018, 2020, 2025).
- 2015–2018 sin títulos; 2015 sin hora de publicación.
- Timestamps de publicación incoherentes (4,4–6,9 %) y re-rastreos antiguos (2,8–7,1 %).
- Publicación tardía de hasta +80 min en el primer fichero tras una caída, y ficheros del índice que dan 404.
- **Fallo de parseo detectado:** en 2020-12-10 08:00 un registro con un salto de línea dentro se partió en dos y `truncate_ragged_lines=True` lo aceptó sin avisar. El parser de F2 debe detectarlo y fallar ruidosamente (§6.3).
- La hora de disponibilidad histórica se apoya en `Last-Modified`, que podría reflejar re-subidas. Su coherencia (siempre 2–12 min antes del nombre, 2015–2026) y el monitor en vivo la hacen creíble, pero no es un registro oficial.
- Solo fuentes en inglés (el índice de traducción, 141 MB, no se auditó).
- Licencia: uso libre con atribución según los términos de GDELT. No se redistribuye contenido de medios; solo metadatos.

## 9. Recomendación (para G0 y F2)
- GDELT es **viable** como fuente de estructura de noticias para 2019/2020–2026. Para 2015–2018 solo con temas y entidades.
- `first_available_at = max(nominal + 15 min, Last-Modified + 5 min)` (ADR-012 revisado); se descarta bajar el margen a +5 min.
- Ingesta F2: filtrar en streaming el GKG (empezando por el piloto 2024–2025) en lugar de descargar los 2,9 TB.
