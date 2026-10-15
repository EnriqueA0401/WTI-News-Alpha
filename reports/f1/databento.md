# F1 — Auditoría de Databento GLBX.MDP3 (precio intradía CL)

Fecha: 2026-10-15. Solo se usaron endpoints gratuitos (`metadata.*` y `symbology.resolve`); **no se ha descargado ningún dato de mercado ni se ha gastado dinero**. Ninguna llamada a `timeseries.get_range` ni a `batch.*`.

- Scripts: `scripts/f1_audit/databento_meta.py` (peticiones, etapas `base`, `docs`, `quotes`, `resolve`, `probe`, `years`) y `scripts/f1_audit/databento_summarize.py` (tablas; sin red; verifica checksums).
- Respuestas crudas: `data/raw/databento_meta/**` (177 ficheros); manifiesto con sha256: `manifests/databento_meta.jsonl`. Las rutas que se citan abajo son relativas a `data/raw/databento_meta/`.
- La re-ejecución de `quotes` no volvió a llamar a la red (caché del RawStore con checksum válido; el manifiesto siguió en 159 líneas). Se buscó la clave API en todos los ficheros generados: 0 coincidencias.

## 1. Que los endpoints usados son gratuitos (antes de llamarlos)

Docs de la API histórica (`docs/2026-10-15_historical_http.html`, que es el contenido de https://databento.com/docs/api-reference-historical):
> "You will only be billed for usage of time series data. Access to metadata, symbology, and account management is free. The metadata.get_cost endpoint can be used to determine cost before you request any data."

> (symbology.resolve) "This conversion can be done, for no cost, with the symbology.resolve endpoint."

Las docs presentan `symbology.resolve` como POST; con GET también respondió `OK`, y solo se usó para días sueltos.

## 2. Dataset, schemas y condición

| Hecho | Valor | Fuente |
|---|---|---|
| Rango del dataset | 2010-06-06T00:00Z → 2026-10-14T21:02:28Z | `dataset_range_glbx.json` |
| Rango de ohlcv-1m/1h/1d, definition, statistics, status, trades, tbbo, mbp-1 | desde 2010-06-06 | ídem |
| mbo, cmbp-1, cbbo-* | desde 2017-05-21 | ídem |
| Schemas | mbo, mbp-1, mbp-10, tbbo, trades, bbo-1s, bbo-1m, ohlcv-1s, ohlcv-1m, ohlcv-1h, ohlcv-1d, definition, statistics, status, cmbp-1, cbbo-1s, cbbo-1m | `list_schemas_glbx.json` |
| Publisher | id 1 = "CME Globex MDP 3.0" | `list_publishers.json` |
| Condición del 2015-02-18 al 2026-10-14 | 3.694 fechas: 3.670 `available`, 24 `degraded`, 0 `missing`/`pending`. Las 563 fechas que no aparecen son todas sábados | `dataset_condition_glbx_2015-02-18_last.json` |

Días `degraded` (en QA hay que marcarlos o excluirlos): 2017-11-13, 2018-10-21, 2019-01-15, 2019-02-22, 2019-03-13, 2019-03-26, 2020-02-27, 2020-02-28, 2020-05-05, 2020-06-30, 2020-07-01, 2021-12-05, 2022-01-02, 2024-09-18, 2025-09-17, 2025-09-24, 2025-11-28, 2026-01-31, 2026-03-15, 2026-03-16, 2026-03-21, 2026-04-10, 2026-05-24, 2026-08-29.

Notas de la documentación del dataset (`docs/2026-10-15_venues.html`):
- "Databento provides full coverage of all futures and options on futures beginning in June 2010." y "Before May 2017, data was based on the legacy FIX/FAST protocol." Por tanto, 2015 a mayo de 2017 sale de otro feed de origen. Ver el riesgo R3.
- Settlement (estadísticas): "Settlement price statistics will first publish shortly after the settlement window for an instrument." `stat_flags` distingue final/preliminar y actual/teórico; `ts_ref` es la fecha de sesión (solo con precisión de día). El volumen compensado y el OI "will normally publish on the following UTC date".
- Schemas (`docs/2026-10-15_schemas.html`): en OHLCV, "If no trade occurs within the interval, no record is printed."; "Our `ohlcv-1d` schema is based on UTC dates." (no por sesión CME). El schema `status` da los cambios de estado de sesión, y "As trading status can carry across UTC days, the historical API includes a snapshot of the last status record for each active instrument at UTC midnight."

## 3. Simbología: cómo pedir solo los outrights de CL

- `CL.FUT` con `stype_in=parent` **incluye los spreads**. La documentación dice "Spreads and combinations consisting only of futures (such as exchange-listed calendar spreads) are considered part of the futures parent symbols" (`docs/2026-10-15_venues.html`); ver también `docs/2026-10-15_standards.html`: "ES.FUT refers to all E-mini S&P 500 futures and futures spreads". El tipo de instrumento solo aparece en `instrument_class` (schema definition), no en las barras.
- Medido con `symbology.resolve`. El 2024-03-01, CL.FUT tenía 1.928 instrumentos: 131 outrights, 1.430 spreads con '-' (p. ej. `CLV4-CLH5`, `CLJ4-WTTJ4`) y 367 UDS `CL:` (`resolve_clfut_parent_to_iid_2024-03-01.json`). El 2015-06-01 tenía 1.362 instrumentos: 72 outrights, 1.123 spreads y 167 UDS (`resolve_clfut_parent_to_iid_2015-06-01.json`).
- **Cómo excluir los spreads:** pedir `stype_in=raw_symbol` con una lista explícita de 552 símbolos, `CL`+mes (FGHJKMNQUVXZ)+año de 1 dígito (0–9) o de 2 dígitos (10–45). El límite es de 2.000 por petición. CME usa año de 2 dígitos en los contratos lejanos: "CME has begun using a 2-digit year" (`docs/2026-10-15_venues.html`). La regla se comprobó en 14 fechas:
  - En 12 días, uno por año de 2015 a 2026 (`resolve_years/*.json`), todos los outrights de CL.FUT encajan en el patrón y no hay símbolos "otros" aparte de 2 UDS `UD:CL:` en 2025. Hay entre 71 y 127 outrights por día. Los años de 2 dígitos aparecen desde 2019 (rango 29–37).
  - Los días 2015-06-01 y 2024-03-01, CL.FUT ⊆ lista. La lista resuelve además 8 y 14 símbolos que no están en CL.FUT (contratos ya vencidos), con **0 registros ohlcv-1m** en su mes (`probe/get_record_count_rawonly_ohlcv-1m_2015-06.json`, `..._2024-03.json`). No añaden coste ni datos.
- Son barras por contrato, sin serie continua. No usar `CL.c.0`/`CL.n.0`, porque el roll lo hacemos nosotros (PROTOCOL A5).

## 4. Cotizaciones exactas (`get_cost`, `get_billable_size`, `get_record_count`)

Precio unitario histórico en USD/GB: ohlcv-1m 70, ohlcv-1h/1d 190, trades/tbbo 28, mbp-1 1,8, definition 1,7, statistics 1,0, status 4,0 (`unit_prices_glbx.json`). "Rango completo" va de 2015-02-18T00:00Z a 2026-10-14T00:00Z. Todos los extremos están alineados a medianoche UTC, porque las docs avisan de que la cotización "may not be accurate for time ranges that are not discrete multiples of 10 minutes… The definition schema is only accurate for discrete multiples of 24 hours". "outrights" es la lista explícita de 552 símbolos. Fuente: `quotes/get_{cost,billable_size,record_count}_<universo>_<schema>_<tag>.json`.

| Petición | Schema | Universo | Coste USD | GB facturables | Registros |
|---|---|---|---|---|---|
| rango completo | ohlcv-1m | CL.FUT | 267,34 | 4,101 | 73.228.706 |
| rango completo | ohlcv-1m | **outrights** | **75,78** | 1,162 | 20.756.056 |
| rango completo | ohlcv-1h | CL.FUT | 62,04 | 0,351 | 6.260.901 |
| rango completo | ohlcv-1h | outrights | 8,82 | 0,050 | 889.941 |
| rango completo | ohlcv-1d | CL.FUT | 6,51 | 0,037 | 657.113 |
| rango completo | ohlcv-1d | outrights | 0,71 | 0,004 | 71.833 |
| rango completo | definition | CL.FUT | 10,76 | 6,794 | 13.065.271 |
| rango completo | definition | outrights | 0,34 | 0,213 | 410.329 |
| rango completo | statistics | CL.FUT | 5,77 | 6,197 | 77.468.081 |
| rango completo | statistics | outrights | 1,30 | 1,391 | 17.389.831 |
| rango completo | status | CL.FUT | 6,42 | 1,724 | 43.107.728 |
| rango completo | status | outrights | 0,45 | 0,122 | 3.045.449 |
| 2024-03 | ohlcv-1m | CL.FUT | 1,71 | 0,026 | 469.147 |
| 2024-03 | ohlcv-1m | outrights | 0,52 | 0,008 | 143.407 |
| 2024 | ohlcv-1m | CL.FUT | 23,34 | 0,358 | 6.392.301 |
| 2024 | ohlcv-1m | outrights | 7,11 | 0,109 | 1.948.164 |
| 2024-03 / 2024 | trades | outrights | 3,88 / 53,70 | 0,149 / 2,059 | 3.098.635 / — |
| 2024-03 / 2024 | tbbo | outrights | 6,46 / 89,49 | 0,248 / 3,432 | 3.098.635 / — |
| 2024-03 / 2024 | mbp-1 | outrights | 7,73 / 151,72 | 4,613 / 90,505 | 57.667.246 / — |
| 2024-03 / 2024 | ohlcv-1s | outrights | 3,69 / 50,59 | 0,057 / 0,776 | 1.011.332 / — |

ohlcv-1m por año (`quotes/*_ohlcv-1m_yYYYY.json`; 2015 empieza el 18-feb y 2026 termina el 14-oct):

| Año | CL.FUT USD | outrights USD | outrights registros |
|---|---|---|---|
| 2015 | 17,08 | 6,74 | 1.846.548 |
| 2016 | 17,48 | 5,26 | 1.440.612 |
| 2017 | 17,85 | 5,44 | 1.491.131 |
| 2018 | 20,40 | 5,73 | 1.569.120 |
| 2019 | 19,56 | 5,50 | 1.506.202 |
| 2020 | 24,40 | 6,83 | 1.870.013 |
| 2021 | 22,44 | 6,46 | 1.770.269 |
| 2022 | 26,67 | 6,65 | 1.821.008 |
| 2023 | 22,04 | 6,61 | 1.811.046 |
| 2024 | 23,34 | 7,11 | 1.948.164 |
| 2025 | 25,52 | 6,73 | 1.842.476 |
| 2026 | 30,58 | 6,72 | 1.839.467 |
| suma | 267,36 | 75,78 | — |

Qué significan estas cotizaciones:
- Son el precio que devuelve la API para esta cuenta. Según las docs, `get_cost` "respects any discounts provided by flat rate plans" y "returns a cost of $0.00 when the requested historical data is included in your plan" (`docs/2026-10-15_pricing_faq_js.js`). Como devuelve importes distintos de cero, estos datos **no están incluidos en ningún plan de la cuenta**.
- La API de metadatos no informa del saldo de créditos. La web dice "Databento provides $125 in free credits when you sign up… expire after six months" (misma fuente). **No sé si esta cuenta conserva créditos**: hay que mirarlo en el portal (Billing).
- Se factura por bytes realmente enviados. El streaming repetido se cobra cada vez, mientras que con descarga batch "you are only billed once… download… multiple times over 30 days" (`docs/2026-10-15_historical_http.html`). Hay que comprar por batch.
- Excluir spreads abarata ×3,5 el ohlcv-1m y ×32 el definition.
- Tamaño de cada descarga:
  - ohlcv-1m outrights del rango completo: 1,16 GB sin comprimir, por debajo del umbral de 2 GB.
  - Por encima del umbral (parada obligatoria de CLAUDE.md §4): CL.FUT en ohlcv-1m (4,1 GB), en definition (6,8 GB) y en statistics (6,2 GB).

## 5. Licencia y términos

Citas exactas, todas guardadas en `docs/`:
- FAQ de precios (https://databento.com/pricing → `docs/2026-10-15_pricing_faq_js.js`):
  - "Historical data can generally be shared internally without a license. This typically means data at least 24 hours old, though some venues use an earlier cutoff."
  - "Your redistribution rights depend on the dataset. Databento doesn't apply any redistribution restrictions. However, for each dataset, we have to pass through the licensing restrictions from the original publisher." / "Most of our datasets can be redistributed internally or externally after 24 hours."
  - "Usage-based pricing (also referred to as pay as you go) is available for historical data only."
- Portal (https://databento.com/docs/portal → `docs/2026-10-15_portal.html`):
  - "As a licensed distributor for 60+ trading venues, Databento redistributes real-time and intraday market data from the last 24 hours."
  - "License fees refer to any monthly charges imposed by venues for the internal and external use … of **live** market data". La tabla de 2026 para CME dice "Personal: Included with Standard plan (up to 2 devices); Commercial: $973/exchange…". Esa tabla es para datos en vivo y no aplica a nuestro uso (histórico de más de 24 h, investigación interna).
- **Lo que no pude verificar:**
  - Los Terms of Service de Databento: https://databento.com/legal es una SPA, y la descarga estática solo devuelve la plantilla sin texto.
  - La política de información de CME sobre datos históricos: cmegroup.com está bloqueado (ADR-005).
  - En la documentación no aparece ninguna tarifa de CME para datos históricos con más de 24 h. Aun así, que no aparezca no es una confirmación.
  - Obligación que se aplica igualmente: no redistribuir los datos. `data/` no va a git y los informes solo llevan agregados.

## 6. Riesgos

- **R1, sesgo de barras vacías.** ohlcv no emite barra si no hay operaciones, así que la ausencia de una barra no prueba que el mercado estuviera cerrado. Para el horario de sesión (ADR-010) se debe usar `status`; las barras de 1 min sirven solo como contraste.
- **R2, ohlcv-1d en días UTC.** No sirve como cierre de sesión ni como settlement. El settlement oficial está en `statistics` (`stat_type`=3, `stat_flags` final/actual). Su `ts_recv` acota por arriba la hora de publicación, no la ventana de settlement en sí. Para fijar esa ventana sigue haciendo falta un documento de CME o deducirla de los datos.
- **R3, feed legado antes de mayo de 2017.** Desconozco cuántos mensajes de `status` hay antes de 2017-05. La cuota no permite saberlo por año porque solo se pidió el rango completo, y no se ha mirado el contenido.
- **R4, días degradados.** Hay 24 días `degraded`, varios en 2020 y 2026. QA debe marcarlos.
- **R5, anomalía de 2015 sin explicar.** 2015 tiene más barras de outrights (6,74 USD para unos 10,5 meses) que 2016–2019. La lista explícita en 2015 no captura nada fuera de CL.FUT con actividad (probe de junio = 0), así que lo atribuyo a la actividad real, pero no está verificado.
- **R6, patrón de símbolos.** La lista de outrights depende del patrón `CL[mes][1–2 dígitos]`. Se verificó en 14 fechas, no en todas. La comprobación definitiva es cruzar con `definition` (`instrument_class`=F) tras la compra.

## 7. Recomendación (requiere aprobación del usuario; no se ha comprado nada)

| Opción | Contenido (batch, DBN, outrights de CL, 2015-02-18→2026-10-14) | Coste cotizado |
|---|---|---|
| **A. Verificación (ADR-010, settlement, roll N, tick, vencimientos)** | definition 0,34 + statistics 1,30 + status 0,45 + ohlcv-1d 0,71 | **2,80 USD** |
| B. Piloto de 1 año | ohlcv-1m del año elegido (de 5,26 a 7,11 según el año, ver la tabla) | ~5–7 USD |
| B'. Piloto de 2 años | p. ej. 2018+2019 = 11,23; 2022+2023 = 13,26 | 11–13 USD |
| **C. Estudio completo** | ohlcv-1m del rango completo | **75,78 USD** |
| A + C | todo lo necesario para F2–F10 en precio | **78,58 USD** |
| (Referencia) igual con CL.FUT y spreads | 267,34 + 10,76 + 5,77 + 6,42 + 6,51 | 296,80 USD (y más de 2 GB) |

Lo que recomiendo:
1. Comprar ya la opción A (2,80 USD). Basta para medir por fecha la apertura, la pausa y el cierre con `status`, el settlement y su hora de publicación con `statistics`, el volumen y el OI alrededor de los vencimientos para fijar N del roll, y los vencimientos y el tick con `definition`.
2. Si G0 sale "intradía viable", comprar C directamente (75,78 USD) en lugar de B+C. El piloto es un subconjunto de C, y comprarlos por separado supone pagar dos veces esos años, unos 11–13 USD. El periodo del test sellado quedará en `data/raw`, pero sin abrir: lo protege la guardia del test sellado (F6).
3. Hacer todas las compras por `batch.submit_job` con la lista de 552 símbolos (`stype_in=raw_symbol`). Justo antes de enviar, repetir `get_cost` con los mismos parámetros y presentar la cifra.

## 8. Preguntas abiertas para el usuario

1. ¿Apruebas la compra A (2,80 USD)? ¿Y C (75,78 USD) si G0 es intradía?
2. ¿Le quedan créditos a la cuenta? Hay que mirarlo en el portal, Billing; la API de metadatos no lo informa.
3. Términos: ¿puedes confirmar en el portal (o en https://databento.com/legal desde un navegador) que los ToS permiten el uso interno de investigación de datos CME históricos sin una tarifa de CME aparte? Yo no pude leerlos desde el entorno.
4. ¿Hacen falta los schemas trades/tbbo para estimar el spread y el slippage reales? En 2024, tbbo de outrights cuesta 89,49 USD/año. Propongo no comprarlos y usar el supuesto de 1 tick por lado (ADR-009). Como mucho, comprar 1 mes de tbbo (6,46 USD) para contrastar.
