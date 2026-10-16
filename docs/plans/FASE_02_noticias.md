# F2 — Noticias normalizadas con `first_available_at` (GDELT GKG filtrado)

**Estado:** G0 aprobado (ADR-019). Reglas ya decididas: A9 y D6 (`first_available_at` = timestamp nominal del fichero + 15 min; ADR-012/014). Este plan las aplica; no fija reglas nuevas.

## Entradas
- `data/raw/gdelt_gkg_oil/gkg-oil-v2/` (2024–2025) y `gkg-oil-v3/` (2018–2020, 2026), un Parquet por día.
- Manifiestos `manifests/gdelt_gkg_oil/<versión>/YYYY-MM.jsonl.gz`: sha256 de cada día, `Last-Modified` de cada fichero de origen y `recorded_at_utc`.
- `config/news.yaml`: versiones y rangos de días.

## Salida
- `data/normalized/gdelt_news/<version>/YYYYMM.parquet`: un fichero por mes, escrito una vez y en solo lectura.
- Manifiesto con sha256 de entradas y salidas, parámetros, código y conteos.

| Columna | Tipo | Significado |
|---|---|---|
| `source_file_ts` | Datetime UTC | Timestamp nominal del fichero GKG de 15 min |
| `row_in_file` | UInt32 | Clave de fila junto con `source_file_ts` (ADR-013) |
| `gkg_record_id` | Utf8 | Id de GDELT (no único en 2015–2018) |
| `filter_version` | Utf8 | `gkg-oil-v2` / `gkg-oil-v3` |
| `first_available_at` | Datetime UTC | `source_file_ts` + 15 min (D6) |
| `file_last_modified_utc` | Datetime UTC, nulo si falta | `Last-Modified` del zip de origen |
| `late_file` | Bool | `Last-Modified` > `first_available_at`: el fichero existió después de la hora asumida (riesgo D6, solo se marca) |
| `ingested_at` | Datetime UTC | `recorded_at_utc` del día en el manifiesto |
| `published_at` | Datetime UTC, nulo | `PAGE_PRECISEPUBTIMESTAMP` de `v2_extras_xml` (semántica UTC no verificada) |
| `exclude_reason` | Utf8, nulo | `published_after_available` si `published_at` > `first_available_at` (CLAUDE.md §7: fecha futura → se marca y se excluye aguas abajo) |
| `url`, `source_common_name`, `title` | Utf8 | Documento, medio, `PAGE_TITLE` (nulo si no hay) |
| `themes`, `organizations`, `persons`, `locations`, `amounts` | Utf8 | Campos V1/V2.1 sin transformar |
| `tone` | Float64 | Primer valor de V1.5 Tone (tono medio) |
| `match_theme`, `match_title`, `match_org`, `match_url` | Bool | Motivo del filtro |

## Invariantes
- Toda marca en UTC. Se cumple `first_available_at > source_file_ts`, y `ingested_at ≥ first_available_at` (si no, el dato es incoherente → error).
- Cada fichero de día se verifica contra su manifiesto (sha256) antes de leerlo.
- Sin deduplicación ni filtros de relevancia: eso es F3. Las filas no se borran; solo se marcan.
- Se escribe mes a mes, en streaming, para no cargar 16 M filas en memoria. Para eso el publicador común admite un escritor que trabaja sobre el directorio de staging.
- Es determinista: orden por (`source_file_ts`, `row_in_file`).

## Tests (antes de implementar)
- `first_available_at` a mano, también en los cambios de horario (UTC puro: no hay DST).
- `published_at`:
  - se parsea;
  - si falta → nulo;
  - si tiene formato inválido → nulo y se cuenta;
  - si es futuro → `exclude_reason`.
- Tono: parseo; si es inválido → error.
- `late_file` con `Last-Modified` > +15 min.
- Integración con raw sintético:
  - dos versiones de filtro;
  - un día con sha256 alterado → error;
  - salida mensual de solo lectura más manifiesto.
- Publicador con escritor en streaming:
  - nombres válidos;
  - fallo → nada publicado.

## Riesgos
- D6: 2 de 68.493 ficheros llegaron después de +15 min. Quedan marcados con `late_file`.
- La semántica de `PAGE_PRECISEPUBTIMESTAMP` no está verificada: solo se usa para marcar incoherencias, nunca para adelantar la disponibilidad.

## Hecho cuando
- `make check` en verde.
- Revisión adversarial resuelta.
- Ejecución completa con informe `reports/f2/gdelt_news_normalized.md`: filas por mes, `late_file`, `published_at` incoherentes y tamaño.
