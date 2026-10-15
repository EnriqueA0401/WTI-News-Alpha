# Diccionario de datos

Convenciones: todos los timestamps son tz-aware en **UTC** (`Datetime(time_zone="UTC")`); America/New_York solo para lógica de sesión. Cada fila lleva `available_at` (o equivalente) explícito. Esquemas `pandera` en cada frontera de capa.

Estado F0: esquemas definidos a nivel de diseño (Documento §5.1); se implementan y validan en F2+.

| Tabla | Capa | Campos clave | Tiempo de disponibilidad | Fase |
|---|---|---|---|---|
| raw_articles | raw | source, url, title, published_at, first_seen_at, ingested_at, language, raw_hash, raw_text?, source_payload | `first_seen_at` (prospectivo) / fichero GDELT + retraso (histórico) | F2 |
| gdelt_gkg_oil (`data/raw/gdelt_gkg_oil/<version>/YYYY/MM/YYYYMMDD.parquet`) | raw (extracto filtrado, ADR-011/013) | 27 columnas GKG 2.1 originales como texto (`gkg_record_id`, `v21_date`, `v2_document_identifier`, `v1_themes`, `v1_organizations`, `v15_tone`, `v2_gcam`, `v2_extras_xml`…); `source_file_ts` (UTC, timestamp nominal del fichero de 15 min); `row_in_file` (línea 0-based); `match_theme`/`match_title`/`match_org`/`match_url`. **Clave de fila: `(source_file_ts, row_in_file)`**; `gkg_record_id` no es único en 2015–2018 y `v2_document_identifier` no siempre es una URL (colección 2 = cita de fuente) | Ninguno en raw. `first_available_at` se calcula en la capa normalizada (ADR-012 pendiente) con `source_file_ts` y el `last_modified_utc` del manifiesto | F2 |
| articles | normalized | article_id, canonical_url, title_clean, published_at_utc, first_available_at_utc, source, language, duplicate_group_id | `first_available_at_utc` | F2–F3 |
| events | events | event_id, event_type, region, entities, event_start_est, first_available_at, novelty, severity, supply_direction, demand_direction, confidence, evidence_ref, extractor_version | `first_available_at` | F3 |
| event_articles | events | event_id, article_id, relation_type, matched_at, match_score | `matched_at` | F3 |
| market_bars | normalized | ts_start_utc, ts_end_utc, contract_id, open, high, low, close, volume, trade_count, bid/ask?, bar_complete, source | `ts_end_utc` (barra completa) | F2 |
| fundamentals | normalized | series_id, observation_period, value, release_time, vintage/as_of_time, source, revision_id | `release_time` | F2 |
| feature_snapshots | features | decision_time, data_cutoff_time, market_features, fundamental_features, event_features, feature_version | `max(available_at) ≤ decision_time` | F4 |
| labels | labels | decision_time, horizon, target_type, target_value, label_version, execution_assumption | — (futuro, separado de features) | F6 |
| predictions / runs | runs | model_id, run_id, prediction_time, predictions, split_id, model_version, calibration_version | — | F7 |

Unidades: precios en USD/bbl; volumen en contratos; tick 0,01 USD/bbl (pendiente de verificar, ADR-005).
