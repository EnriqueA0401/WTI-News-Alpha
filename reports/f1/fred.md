# F1 — Auditoría FRED / ALFRED

Fecha de auditoría: 2026-10-15 (descargas entre 07:34 y 07:38 UTC). Todas las cifras proceden de ficheros en `data/raw/fred/` registrados en `manifests/fred.jsonl` (84 ficheros, 4,8 MB, checksums verificados). La API de FRED no devuelve la clave en el cuerpo, y la clave no aparece en ningún fichero raw, manifiesto ni script (comprobado con grep).

Scripts (`scripts/f1_audit/`): `fred_fetch.py`, `fred_audit_fetch.py` (descargas), `fred_analyze.py` (cobertura, vintages, revisiones), `fred_g17_crosscheck.py`.

Por serie se guardó en `data/raw/fred/api/<SERIE>/`:
- `series.json`;
- `vintagedates.json` (todas las vintages);
- `release.json`;
- `obs_latest_2015.json`;
- `obs_initial_2015.json` (`output_type=4`, valor de primera publicación; solo para series con ≤ 2000 vintages);
- `obs_asof_2020-01-02.json` (foto as-of).

Por release: `data/raw/fred/api/release_<id>/dates_2015.json`. Páginas oficiales de horario en `data/raw/fred/official/`.

## 1. Comprobaciones realizadas

| # | Comprobación | Resultado |
|---|---|---|
| 1 | Metadatos y cobertura 2015–hoy de las 11 series | OK, las 11 cubren 2015-01-01 a sep/oct-2026 |
| 2 | Vintages ALFRED hasta 2015 | Sí en 10/11. **DTWEXBGS: la primera vintage es 2019-02-04** |
| 3 | Fechas de release (`fred/release/dates`) | Disponibles para todas. G.17 contrastado con la Fed: 140/140 |
| 4 | Revisiones medidas (inicial vs actual; as-of 2020-01-02 vs actual) | Ver §3 |
| 5 | Hora oficial de publicación | Verificada en la web de la Fed solo para H.15, H.10 y G.17 (página vigente). **BLS y DOL: HTTP 403** desde el entorno, así que no verificada |
| 6 | Límite de la API | `output_type=4` falla con HTTP 400 si hay más de 2000 vintages ("exceeds the maximum number of vintage dates allowed … (2000)"). Afecta a DGS10, DGS2, T10YIE y VIXCLS; para ellas se usó la foto as-of |

## 2. Cobertura, vintages y releases

Fuente: `fred_analyze.py` sobre `data/raw/fred/api/*`. "Obs" = observaciones desde 2015-01-01 (las "." son días sin dato). "Vint. ≥2015" = fechas de vintage desde 2015-01-01.

| Serie | Frec. | Unidades | Obs (".") | Último dato | Vintages (1ª) | Vint. ≥2015 | Release FRED (id) | Fechas release ≥2015 |
|---|---|---|---|---|---|---|---|---|
| DCOILWTICO | D | $/bbl | 3069 (133) | 2026-10-06 | 801 (2011-04-06) | 608 | Spot Prices, EIA (212) | 611 |
| DTWEXBGS | D | Índice ene-2006=100 | 3072 (136) | 2026-10-09 | 402 (**2019-02-04**) | 402 | H.10 (17) | 615 |
| DGS10 | D | % | 3074 (128) | 2026-10-13 | 5129 (2005-06-28) | 2909 | H.15 (18) | 2925 |
| DGS2 | D | % | 3074 (128) | 2026-10-13 | 5130 (2005-06-28) | 2909 | H.15 (18) | 2925 |
| T10YIE | D | % | 3075 (128) | 2026-10-14 | 3145 (2014-01-27) | 2915 | Interest Rate Spreads (304, calculado por FRED) | 2934 |
| VIXCLS | D | Índice | 3074 (79) | 2026-10-13 | 3951 (2010-11-22) | 2947 | CBOE Market Statistics (200) | 2946 |
| INDPRO | M | Índice 2017=100, SA | 140 (0) | 2026-08-01 | 1223 (1927-01-26) | 150 | G.17 (13) | 150 |
| PAYEMS | M | Miles de personas, SA | 141 (0) | 2026-09-01 | 860 (1955-05-06) | 142 | Employment Situation (50) | 143 |
| UNRATE | M | %, SA | 141 (1) | 2026-09-01 | 800 (1960-03-15) | 142 | Employment Situation (50) | 143 |
| CPIAUCSL | M | Índice 1982-84=100, SA | 141 (1) | 2026-09-01 | 670 (1972-07-21) | 149 | CPI (10) | 149 |
| ICSA | W (sábado) | Número, SA | 614 (0) | 2026-10-03 | 901 (2009-05-28) | 608 | UI Weekly Claims (180) | 612 |

**Retraso de primera publicación** (días entre la fecha de la observación y el `realtime_start` de su primera vintage, `obs_initial_2015.json`):

| Serie | Mín. | Mediana | Máx. |
|---|---|---|---|
| DCOILWTICO | 0 | 6 | 15 |
| DTWEXBGS | 3 | 5 | 10 |
| INDPRO | 42 | 45 | 93 |
| PAYEMS | 31 | 34 | 80 |
| CPIAUCSL | 37 | 42 | 56 |
| ICSA | 4 | 5 | 54 |

- Los máximos de las series macro son todos de sep–dic 2025:
  - ICSA: semanas del 2025-09-27 al 2025-10-18, primera vintage el 2025-11-20.
  - PAYEMS: 2025-09, primera vintage el 2025-11-20.
  - INDPRO: 2025-09, primera vintage el 2025-12-03.
  - CPIAUCSL y UNRATE tienen "." en 2025-10.

  La causa no está verificada en fuente oficial en esta auditoría.
- La fecha de vintage ALFRED es la fecha en que FRED registró el dato. Normalmente coincide con la publicación oficial, pero no lo garantiza, y **nunca da la hora**.

**Contraste G.17** (`fred_g17_crosscheck.py`, `data/raw/fred/official/fed_g17_release_dates_2026-10-15.html`):
- Las 140 fechas mensuales que publica la Fed entre 2015 y hoy están todas en FRED.
- FRED tiene 10 fechas más (2015-07-21, 2016-04-01, 2017-03-31, 2018-03-23, 2019-03-27, 2021-05-28, 2022-06-28, 2023-03-28, 2024-06-28 y 2025-11-24), que son también vintages de INDPRO. Probablemente sean revisiones anuales (la página dice "The annual revision is issued at noon"), pero no está verificado fecha a fecha.

## 3. Revisiones medidas

"Inicial vs actual" = valor de primera publicación frente al valor actual (obs 2015+). "As-of" = foto con `realtime_start = realtime_end = 2020-01-02` frente al valor actual (obs 2015-01-01 a 2019-12-31).

| Serie | Inicial vs actual: % obs revisadas | Mediana \|rev.\| (rel.) | Máx. \|rev.\| | As-of 2020 vs actual: % revisadas |
|---|---|---|---|---|
| DCOILWTICO | 0,2 % (5 de 2916) | 4,61 $ (22 %) | 6,48 $ | 0 % (0 de 1241) |
| DTWEXBGS | **90,7 %** (1742 de 1921) | 0,30 (0,26 %) | 2,18 | **99,9 %** (1239 de 1240) |
| DGS10 | n/d (>2000 vintages) | — | — | 0 % (0 de 1250) |
| DGS2 | n/d | — | — | 0 % (0 de 1250) |
| T10YIE | n/d | — | — | 0 % (0 de 1250) |
| VIXCLS | n/d | — | — | 0 % (0 de 1259) |
| INDPRO | **99,3 %** | 4,38 (4,0 %; incluye cambio de base) | 9,04 | 100 % (59 de 59) |
| PAYEMS | **99,3 %** | 268 mil (0,18 %) | 1246 mil | 100 % |
| CPIAUCSL | **93,6 %** | 0,158 (0,06 %) | 0,72 | 100 % |
| UNRATE | 41,4 % | 0,1 pp | 0,1 pp | 22 % |
| ICSA | **92,7 %** | 5000 (1,8 %) | 702.000 | 85,1 % |

Detalle de DCOILWTICO (`obs_initial_2015.json` vs `obs_latest_2015.json`):
- **29 valores** publicados inicialmente fueron borrados después (pasan a "."). Muchos caen en festivos (2015-01-01 inicial 53,45; 2018-12-25 inicial 45,38); otros no (2015-01-08, 2015-01-26 a 2015-01-29, 2022-12-26).
- **20 fechas** se publicaron primero como "." y se rellenaron después. Entre ellas, el bloque 2024-05-31 → 2024-06-25: su primera vintage es del 2024-07-03 y vino en ".".
- 2020-03-17 → 2020-03-23: valores iniciales **desplazados un día** (por ejemplo, 2020-03-18 inicial 26,96, actual 20,48), corregidos después.
- 2021-09-27: inicial 73,43, actual 75,54.
- La serie coincide al 100 % con RWTC de la API EIA en 2931 fechas comunes (ver `eia.md`).

## 4. Hora oficial de publicación

| Release | Series | Hora oficial verificada | Fuente (raw) | Semántica para point-in-time |
|---|---|---|---|---|
| H.15 | DGS10, DGS2 (y la base de T10YIE) | "posted daily Monday through Friday at 4:15pm"; "not posted on holidays" | `data/raw/fred/official/fed_h15_2026-10-15.html` | La publicación del 2026-10-14 trae columnas hasta el 2026-10-13: el dato del día d se publica en d+1 hábil a las 16:15 ET |
| H.10 | DTWEXBGS | "On Mondays at 4:15 p.m. … for the previous business week. If Monday falls on a Federal Holiday … following business day" | `data/raw/fred/official/fed_h10_2026-10-15.html` | El dato del día d está disponible el lunes siguiente a las 16:15 ET (retraso medido de 3–10 días) |
| G.17 | INDPRO | "The monthly releases are issued at 9:15 a.m. The annual revision is issued at noon." | `data/raw/fred/official/fed_g17_2026-10-15.html` | Fechas históricas verificadas (140/140) |
| Employment Situation (BLS) | PAYEMS, UNRATE | **hora no verificada** (bls.gov → HTTP 403) | — | — |
| CPI (BLS) | CPIAUCSL | **hora no verificada** (HTTP 403) | — | — |
| UI Weekly Claims (DOL) | ICSA | **hora no verificada** (dol.gov → HTTP 403) | — | — |
| CBOE Market Statistics | VIXCLS | **hora no verificada** (es un cierre de mercado; FRED no da la hora) | — | — |
| Interest Rate Spreads (FRED) | T10YIE | **hora no verificada** (serie calculada por FRED) | — | Conservador: no antes que H.15 |
| Spot Prices (EIA) | DCOILWTICO | **hora no verificada**; publicación semanal con el WPSR | `data/raw/eia/web/dnav_pet_pri_spt_s1_d_2026-10-15.html` | Disponible solo en la fecha de publicación semanal (ver `eia.md`) |

Las horas de la Fed salen de las páginas **vigentes**. No está verificado que esas mismas horas rigieran en todo 2015–2026.

## 5. Riesgos

1. **Sin hora verificada** para BLS (PAYEMS, UNRATE, CPI) y DOL (ICSA): sus webs no son accesibles desde el entorno. No se sustituye por otra fuente sin aprobación (§4).
2. **DTWEXBGS**: no hay vintages antes del 2019-02-04 y la revisión es casi universal (99,9 % de 2015–2019 revisado frente a la foto de 2020). Antes de 2019-02 no es point-in-time.
3. ALFRED da fechas de registro en FRED, no horas. En casos anómalos (sep–dic 2025) pueden no coincidir con la publicación oficial.
4. DCOILWTICO/RWTC: revisiones esporádicas pero grandes (desplazamiento de un día en marzo de 2020) y publicación semanal. Solo vale como Plan B diario con `available_at` = publicación semanal.
5. Las series diarias con más de 2000 vintages solo se pudieron contrastar con una foto as-of (2015–2019). No se midieron revisiones de 2020 en adelante (se puede hacer con más fotos as-of).

## 6. Recomendación

| Serie | Uso propuesto | Motivo |
|---|---|---|
| DGS10, DGS2 | **Usable point-in-time**: `available_at` = fecha de vintage + 16:15 ET (H.15) | Vintages desde 2005; 0 revisiones 2015–2019; hora oficial (vigente) |
| T10YIE | Usable con `available_at` = vintage + 16:15 ET (conservador, igual que H.15) | Vintages desde 2014; 0 revisiones 2015–2019; hora no oficial |
| VIXCLS | Usable con `available_at` = vintage FRED (día siguiente) a una hora conservadora; **hora no verificada** | 0 revisiones 2015–2019 |
| INDPRO | Usable point-in-time vía ALFRED (`realtime_start` + 9:15 ET; revisiones anuales a las 12:00) | Muy revisada (99 %); hay vintages y hora oficial |
| PAYEMS, UNRATE, CPIAUCSL, ICSA | Usable vía ALFRED en cuanto a **fecha**; **"hora no verificada"** hasta acceder a las fuentes BLS/DOL | Muy revisadas: usar siempre la vintage, nunca el valor actual |
| DTWEXBGS | **Descartar para 2015–2019**, o usar solo desde 2019-02-04; marcar "riesgo de revisión" | Sin vintages antes de 2019 |
| DCOILWTICO | Solo Plan B diario o control (spot Cushing ≠ CL); `available_at` = publicación semanal EIA | Retraso de 0–15 días; revisiones esporádicas |

Prioridad práctica para el modelo intradía de CL: DGS10/DGS2/T10YIE/VIXCLS como contexto de mercado diario. Las macro mensuales y semanales solo aportan como eventos de calendario o sorpresas, y para eso hace falta la hora verificada.

## 7. Preguntas abiertas (decisión del usuario)

1. BLS/DOL devuelven 403 al cliente del entorno. ¿Se autoriza otra vía? Opciones: ampliar la allowlist de red, que el usuario aporte los calendarios oficiales, o aceptar "hora no verificada" y excluir esas series de las features intradía.
2. DTWEXBGS: ¿descartar, usar solo desde 2019-02-04, o sustituir por un índice de dólar de mercado (requiere otra fuente y aprobación)?
3. ¿Se descargan más fotos as-of (por ejemplo, anuales de 2021 a 2026) para medir revisiones de las series diarias después de 2020? Coste: ~4 peticiones por serie y año, < 5 MB.
4. Hora para VIXCLS y T10YIE: ¿se acepta una política conservadora (día hábil siguiente, 16:15 ET) sin hora oficial?
