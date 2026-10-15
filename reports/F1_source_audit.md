# F1 — Informe de auditoría de fuentes (puerta G0)

Fecha: 2026-10-15. Informes de detalle en `reports/f1/` (`gdelt.md`, `databento.md`, `eia.md`, `fred.md`, `cftc.md`, `noaa.md`). Todas las cifras salen de ficheros en `data/raw/` registrados con sha256 en `manifests/`. No se ha comprado ningún dato.

## 1. Resumen por fuente

| Fuente | ¿Utilizable? | Hora de disponibilidad | Problemas principales |
|---|---|---|---|
| **CL intradía (Databento GLBX.MDP3)** | Sí, pagando | `ts` de evento del exchange (barra completa) | No comprado aún. 24 días `degraded` y 0 `missing` en 2015–2026. Sesiones y settlement a verificar con `status`/`statistics`. ToS no leídos desde el entorno. |
| **GDELT 2.0** | Sí (estructura); títulos solo desde 2019/2020 | Timestamp del fichero. Medido: se publica 2–12 min **antes** del nombre en todos los años | Huecos (≈ 2 %, uno de 17 días en 2025). GKG ≈ 2,9 TB: hay que filtrarlo en streaming. 5–7 % de fechas de publicación incoherentes. |
| **EIA WPSR** | Sí, con reconstrucción | Fechas 2015–2026 verificadas (612). Horas: 10:30 ET estándar; **77 publicaciones desplazadas sin hora verificada** | La API da valores actuales, no la primera publicación: hay que reconstruirla desde el archivo. Redefinición de crudo comercial en oct-2016 (−31 a −34 M bbl). |
| **FRED/ALFRED** | Parcial | Fecha por vintage; hora verificada solo para Fed (H.15, H.10, G.17) | BLS/DOL (empleo, IPC, solicitudes de desempleo): 403 desde el entorno, hora no verificada. DTWEXBGS sin vintages antes de 2019-02-04. |
| **CFTC COT** | Sí | Regla oficial: 3.er día hábil federal tras el martes, 15:30 ET. Hora real por fila desde oct-2022 (90 % entre 15:30 y 15:31) | 2015–2022 sin hora por fila. 23 desplazamientos por festivo sin confirmación oficial. Puesta al día tras el cierre de 2018–19. |
| **NOAA Storm Events** | **No como feature** | Sin hora de publicación; retraso ≥ 80–99 días; revisiones posteriores | Solo contexto retrospectivo (A11). |
| **Plan B diario** | Disponible | RWTC (spot Cushing) se publica semanal; DCOILWTICO en FRED | No es el futuro CL. RWTC viene de Refinitiv/LSEG (licencia a revisar). |

## 2. Coste medido de los datos de precio (Databento, a precio de lista; solo outrights CL)

| Compra | USD | Para qué |
|---|---|---|
| **A**: definition + statistics + status + ohlcv-1d (2015–2026) | **2,80** | Horario de sesión por fecha (ADR-010), settlement y su hora, volumen y OI para fijar N del roll. |
| **C**: ohlcv-1m 2015-02-18 → 2026-10-14 | **75,78** (1,16 GB) | Barras intradía de todo el estudio. Un año suelto cuesta 5,26–7,11 USD. |
| Opcional: tbbo 1 mes | 6,46 | Medir spread/slippage real; si no, se mantiene 1 tick por lado. |

Total A + C = 78,58 USD. `get_cost` devuelve importes distintos de cero: no hay datos incluidos en el plan. Los créditos restantes de la cuenta no son visibles por API.

## 3. Recomendación G0
**Intradía viable, condicionado.** Motivos:
1. El precio intradía de CL por contrato existe, con buena calidad declarada y coste bajo (≈ 79 USD el estudio completo).
2. Las noticias tienen hora de disponibilidad verificable y conservadora (GDELT publica antes del timestamp nominal).
3. Los fundamentales tienen calendario reconstruible, con huecos acotados que se tratan de forma conservadora.

Condiciones (cada una es una decisión del usuario):
- Comprar **A** primero (2,80 USD) y verificar sesiones y settlement antes de comprar **C**.
- Confirmar en el portal de Databento que los ToS permiten este uso interno de investigación, y si hay créditos.
- Ventana de estudio principal **2020–2026**, donde GDELT trae títulos (100 % desde 2020). 2015–2019 queda como análisis secundario con temas y entidades.
- Política conservadora para publicaciones sin hora verificada (EIA desplazadas, COT 2015–2022, BLS/DOL): dato disponible al cierre de la sesión de ese día, o exclusión.

Si no se aprueba la compra: **alternativa diaria/prospectiva** con DCOILWTICO/RWTC (documentando que no es el futuro CL) y captura prospectiva de noticias.

## 4. Decisiones requeridas (resumen)
| # | Decisión | Recomendación |
|---|---|---|
| D1 | Puerta G0 | Intradía viable condicionado (§3). |
| D2 | Compra Databento A (2,80 USD) | Sí. |
| D3 | Compra Databento C (75,78 USD) tras verificar A | Sí, si D1 = intradía. |
| D4 | Ventana principal | 2020–2026; 2015–2019 secundaria. |
| D5 | ADR-011: ingesta GDELT por filtrado en streaming (piloto 2024–2025 ≈ 400 GB de transferencia, ≈ 1,5–3 h) | Sí. |
| D6 | ADR-012: margen GDELT +5 min (en vez de +15) | Sí. |
| D7 | EIA: descargar ≈ 1.200 CSV del archivo (≈ 10 MB) para reconstruir la primera publicación | Sí. |
| D8 | Horas no verificadas (EIA desplazadas, COT 2015–2022, BLS/DOL) | Disponible al cierre de la sesión de ese día. |
| D9 | Margen COT | 15:30 ET + 2 h en 2015–2022; hora real (`created_at`) desde oct-2022. |
| D10 | DTWEXBGS | Usar solo desde 2019-02-04. |
| D11 | NOAA | Fuera de las features; el clima se cubre con noticias. Auditar los avisos NHC más adelante, si hace falta. |
| D12 | Captura prospectiva (GDELT + hora real de WPSR/COT) | Diseñarla en F2 con GitHub Actions. |
