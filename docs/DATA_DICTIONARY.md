# Diccionario de datos

Convenciones: todos los timestamps son tz-aware en **UTC** (`Datetime(time_zone="UTC")`); America/New_York solo para lógica de sesión. Cada fila lleva `available_at` (o equivalente) explícito. Esquemas `pandera` en cada frontera de capa.

Estado F0: esquemas definidos a nivel de diseño (Documento §5.1); se implementan y validan en F2+.

| Tabla | Capa | Campos clave | Tiempo de disponibilidad | Fase |
|---|---|---|---|---|
| raw_articles | raw | source, url, title, published_at, first_seen_at, ingested_at, language, raw_hash, raw_text?, source_payload | `first_seen_at` (prospectivo) / fichero GDELT + retraso (histórico) | F2 |
| gdelt_gkg_oil (`data/raw/gdelt_gkg_oil/<version>/YYYY/MM/YYYYMMDD.parquet`) | raw (extracto filtrado, ADR-011/013) | 27 columnas GKG 2.1 originales como texto (`gkg_record_id`, `v21_date`, `v2_document_identifier`, `v1_themes`, `v1_organizations`, `v15_tone`, `v2_gcam`, `v2_extras_xml`…); `source_file_ts` (UTC, timestamp nominal del fichero de 15 min); `row_in_file` (línea 0-based); `match_theme`/`match_title`/`match_org`/`match_url`. **Clave de fila: `(source_file_ts, row_in_file)`**; `gkg_record_id` no es único en 2015–2018 y `v2_document_identifier` no siempre es una URL (colección 2 = cita de fuente) | Ninguno en raw. `first_available_at` se calcula en la capa normalizada (ADR-012 pendiente) con `source_file_ts` y el `last_modified_utc` del manifiesto | F2 |
| capture observations (rama `capture-data`, `observations/YYYY-MM.jsonl`) | raw prospectivo (ADR-016) | `source` (gdelt / eia_wpsr / cftc_cot), `item` (gdelt: `<fichero>|listed` o `<fichero>|available`; eia: `<semana>|<Last-Modified>`; cot: `report_date`), `observed_at_utc`, `previous_check_utc`, `details` (md5, tamaño, `last_modified_utc`, `release_not_before_utc`, `created_at_utc`…), `error`, `code_commit`, `run_id` | Primera aparición ∈ `(previous_check_utc, observed_at_utc]` | F2 |
| articles | normalized | article_id, canonical_url, title_clean, published_at_utc, first_available_at_utc, source, language, duplicate_group_id | `first_available_at_utc` | F2–F3 |
| events | events | event_id, event_type, region, entities, event_start_est, first_available_at, novelty, severity, supply_direction, demand_direction, confidence, evidence_ref, extractor_version | `first_available_at` | F3 |
| event_articles | events | event_id, article_id, relation_type, matched_at, match_score | `matched_at` | F3 |
| market_bars | normalized | ts_start_utc, ts_end_utc, contract_id, open, high, low, close, volume, trade_count, bid/ask?, bar_complete, source | `ts_end_utc` (barra completa) | F2 |
| fundamentals | normalized | series_id, observation_period, value, release_time, vintage/as_of_time, source, revision_id | `release_time` | F2 |
| feature_snapshots | features | decision_time, data_cutoff_time, market_features, fundamental_features, event_features, feature_version | `max(available_at) ≤ decision_time` | F4 |
| labels | labels | decision_time, horizon, target_type, target_value, label_version, execution_assumption | — (futuro, separado de features) | F6 |
| predictions / runs | runs | model_id, run_id, prediction_time, predictions, split_id, model_version, calibration_version | — | F7 |

Unidades: precios en USD/bbl; volumen en contratos; tick 0,01 USD/bbl (pendiente de verificar, ADR-005).

## Capa de features `feat-v1` (`data/features/feat-v1/features_YYYYMM.parquet`, F4)
Una fila por `decision_time`, cada 5 min en UTC, cuando la última barra completa tiene ≤ 5 min. Código en `src/wti_news_alpha/features/`; configuración en `config/features.yaml`. Ningún valor se rellena: lo ausente es nulo.

| Columna | Unidad | Definición | Disponibilidad |
|---|---|---|---|
| `decision_time`, `data_cutoff_time` | UTC | instante de decisión; el corte es el mismo instante | — |
| `feature_version` | texto | `feat-v1` | — |
| `mkt_close`, `mkt_spread` | USD (bid) | cierre bid y spread medio de la última barra de 1 min | `mkt_available_at` = fin de esa barra |
| `mkt_ret_{1,5,15,60}m` | log | `log(cierre_t / cierre_{t-k})`; nulo si la barra de t-k tiene > 5 min o hay un corte (hueco > 5 min) entre ambas | idem |
| `mkt_rv_60m`, `mkt_range_60m`, `mkt_tick_volume_60m` | log / ticks | volatilidad realizada (√Σ r₁²), log(máx/mín) y volumen de ticks en (t-60, t]; nulo si la ventana cruza un corte | idem |
| `mkt_minutes_since_bar`, `mkt_minutes_since_break` | min | antigüedad de la última barra; minutos desde el último corte (≈ reapertura) | idem |
| `wpsr_<serie>_value`, `wpsr_<serie>_chg` | miles de bbl, miles de bbl/d o % | último valor semanal publicado y cambio frente a la semana anterior exacta (11 series) | `wpsr_available_at`; `wpsr_age_h` = horas desde la publicación |
| `cot_mm_net`, `cot_mm_net_chg`, `cot_mm_net_oi` | contratos / fracción | neto *managed money*, cambio semanal y neto / interés abierto | `cot_available_at`; `cot_age_h` |
| `fred_<serie>_value`, `fred_<serie>_obs_lag_d` | unidades de FRED / días | última observación publicada (vintage vigente) y días desde ella; los festivos sin valor no cuentan | `fred_<serie>_available_at` |
| `news_n_<clave>_{15,60,240}m`, `news_new_<clave>_{15,60,240}m` | artículos / eventos | artículos elegibles únicos y eventos nuevos en (t-w, t]; `<clave>` = `all` o una de las 5 familias; nulo si la ventana empieza antes de la cobertura de GDELT | `news_available_at` = último artículo elegible |
| `news_title_share_240m` | fracción | proporción con título real en 4 h: indicador de época (ADR-033/034) | idem |
| `news_event_age_min_<familia>` | min | minutos desde el último evento nuevo de la familia | idem |
| `cal_hour_et`, `cal_weekday_et` | h / 1-7 | hora decimal y día (lunes = 1) en America/New_York | conocidas de antemano |
| `cal_min_to_wpsr_std`, `cal_min_to_cot_std` | min | minutos al próximo miércoles 10:30 ET o viernes 15:30 ET (calendario estándar; no recoge festivos) | regla fija |

No hay mecanismo ni dirección de las reglas (ADR-032). Las normalizaciones (por hora o régimen) se ajustan en F7, dentro de cada fold.
