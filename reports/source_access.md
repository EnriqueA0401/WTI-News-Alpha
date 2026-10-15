# Acceso a fuentes de datos

Comprobado: 2026-10-15T03:28:12+00:00 (UTC). Generado por `wna sources check`.

| Fuente | Estado | HTTP | Detalle | Clave en entorno | URL sondeada |
|---|---|---|---|---|---|
| gdelt_files | ok | 200 | HTTP 200 | no | https://data.gdeltproject.org/gdeltv2/lastupdate.txt |
| gdelt_doc_api | rate_limited | 429 | HTTP 429 | no | https://api.gdeltproject.org/api/v2/doc/doc?query=%22crude%20oil%22&mode=artlist&maxrecords=1&format=json |
| eia_api | ok | 200 | HTTP 200 | sí | https://api.eia.gov/v2/ |
| eia_wpsr_schedule | ok | 200 | HTTP 200 | no | https://www.eia.gov/petroleum/supply/weekly/schedule.php |
| fred_api | ok | 200 | HTTP 200 | sí | https://api.stlouisfed.org/fred/series?series_id=DCOILWTICO&file_type=json |
| alfred_web | ok | 200 | HTTP 200 | no | https://alfred.stlouisfed.org/ |
| cftc_cot_schedule | ok | 200 | HTTP 200 | no | https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm |
| cftc_cot_api | ok | 200 | HTTP 200 | no | https://publicreporting.cftc.gov/resource/72hh-3qpy.json?$limit=1 |
| noaa_storm_events | ok | 200 | HTTP 200 | no | https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/ |
| databento_api | ok | 200 | HTTP 200 | sí | https://hist.databento.com/v0/metadata.list_datasets |
| cme_specs | unreachable | — | ReadTimeout | no | https://www.cmegroup.com/trading/files/WTI-Fact-Card.pdf |
