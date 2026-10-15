# F1 — Informe de auditoría de fuentes (puerta G0)

Fecha: 2026-10-15. Revisado tras la revisión adversarial independiente (§7).
- Detalle en `reports/f1/` (`gdelt.md`, `databento.md`, `eia.md`, `fred.md`, `cftc.md`, `noaa.md`).
- Todas las cifras salen de ficheros en `data/raw/` registrados con sha256 en `manifests/`. No se ha comprado ningún dato.

## 1. Resumen por fuente

| Fuente | ¿Utilizable? | Hora de disponibilidad | Problemas principales |
|---|---|---|---|
| **CL intradía (Databento GLBX.MDP3)** | Sí, pagando | En OHLCV, `ts_event` es la **apertura** de la barra (según la documentación de Databento; hay que confirmarlo con datos). Propuesta: `available_at = ts_event + duración de la barra` | No comprado. 24 días `degraded` y 0 `missing` en 2015–2026. Sesiones y settlement a verificar con `status`/`statistics`. ToS no leídos desde el entorno. |
| **GDELT 2.0** | Sí (estructura); títulos solo desde 2019/2020 | En slots normales el fichero sale 2–12 min antes del nombre. **Tras una caída, hasta +80 min tarde**; degradados hasta +2 min. Propuesta: `max(nominal + 15 min, Last-Modified + 5 min)` | Huecos: 1,78 % del periodo (uno de 17 días en 2025). **2.859 slots degradados** (< 200 KB), sobre todo mar–nov 2017. Ficheros del índice que dan 404. GKG ≈ 2,9 TB: filtrado en streaming. 4,4–6,9 % de fechas de publicación incoherentes. |
| **EIA WPSR** | Sí, con reconstrucción | Fechas 2015–2026 verificadas (612). **Ninguna hora histórica está verificada oficialmente.** 10:30 ET es el estándar publicado hoy. Además, 77 publicaciones en día distinto de miércoles y los miércoles de semanas festivas | La API da valores actuales, no la primera publicación: hay que reconstruirla desde el archivo. Redefinición del crudo comercial en oct-2016 (−31 a −34 M bbl). |
| **FRED/ALFRED** | Parcial | La vintage da la fecha, nunca la hora; puede ser **el mismo día** que la observación (VIXCLS). Hora verificada solo para la Fed (H.15, H.10, G.17) | BLS/DOL: 403 desde el entorno. DTWEXBGS sin vintages antes de 2019-02-04. El retraso observación→vintage de las series diarias no está medido. |
| **CFTC COT** | Sí | Regla oficial: 3.er día hábil federal tras el martes, 15:30 ET. Hora real por fila desde oct-2022 (90 % entre 15:30 y 15:31) | 2015–2022 sin hora por fila. La regla no incluye cierres federales por orden ejecutiva: errores de días enteros en la dirección que filtra. 23 desplazamientos por festivo sin confirmar. |
| **NOAA Storm Events** | **No como feature** | Sin hora de publicación; retraso ≥ 80–99 días; revisiones posteriores | Solo contexto retrospectivo (A11). |
| **Plan B diario** | Disponible | RWTC (spot Cushing) se publica semanal; DCOILWTICO en FRED | No es el futuro CL. RWTC viene de Refinitiv/LSEG (licencia a revisar). |

## 2. Coste medido de los datos de precio (Databento, a precio de lista; solo outrights CL)

| Compra | USD | Para qué |
|---|---|---|
| **A**: definition + statistics + status + ohlcv-1d (2015–2026) | **2,80** | Horario de sesión por fecha (ADR-010), settlement y su hora, volumen y OI para fijar N del roll. |
| **C**: ohlcv-1m 2015-02-18 → 2026-10-14 | **75,78** (1,16 GB) | Barras intradía de todo el estudio. Un año suelto cuesta 5,26–7,11 USD. |
| Opcional: tbbo 1 mes | 6,46 | Medir el spread y el slippage real; si no, se mantiene 1 tick por lado. |

- Total A + C = 78,58 USD.
- `get_cost` devuelve importes distintos de cero: no hay datos incluidos en el plan.
- Los créditos restantes no son visibles por API.

## 3. Regla conservadora única para horas no verificadas (R-PIT, propuesta)
Sustituye a las anteriores D8/D9, que eran contradictorias (la revisión mostró que "cierre de sesión" = 14:30 ET adelantaba datos):
1. **Fecha verificada, hora no verificada:** `available_at` = **18:00 ET de ese día**. Es posterior a la hora oficial más tardía conocida (EIA, lunes a las 17:00 ET) y a 15:30 + 2 h (COT).
2. **Fecha no verificada** (desplazamientos por festivo sin confirmar, cierres no documentados): **18:00 ET del siguiente día hábil**, o exclusión.
3. **Hora real disponible:** COT `created_at` desde oct-2022 y monitor prospectivo. Se usa la hora real + margen pequeño.
4. **ALFRED** (vintage sin hora): **00:00 ET del día siguiente** a la fecha de vintage.

## 4. Recomendación G0
**Intradía viable, condicionado.**
1. El precio intradía por contrato existe, con buena calidad declarada y coste bajo (≈ 79 USD el estudio completo).
2. Las noticias tienen una regla de disponibilidad verificable y conservadora (§1 y ADR-012), con huecos y degradaciones que se tratan como dato ausente.
3. Los fundamentales tienen calendario reconstruible.
4. **Riesgo central:** la hora de publicación del WPSR no está verificada oficialmente. Si se aplica R-PIT a todas las publicaciones, se pierde la reacción intradía al WPSR, que es uno de los eventos más informativos. Ver D8.

Si no se aprueba la compra: **alternativa diaria/prospectiva** con DCOILWTICO/RWTC (documentando que no es el futuro CL) y captura prospectiva.

## 5. Decisiones requeridas
| # | Decisión | Recomendación |
|---|---|---|
| D1 | Puerta G0 | Intradía viable condicionado (§4). |
| D2 | Compra Databento A (2,80 USD) | Sí. |
| D3 | Compra Databento C (75,78 USD) tras verificar A | Sí, si D1 = intradía. |
| D4 | Ventana principal | 2020–2026, donde hay títulos. Ojo: incluye los dos peores años de huecos (2020: 2.585 slots; 2025: 1.681 con 17 días seguidos). 2015–2019 queda como análisis secundario. |
| D5 | ADR-011: ingesta GDELT por filtrado en streaming (piloto 2024–2025 ≈ 400 GB de transferencia, ≈ 1,5–3 h) | Sí. |
| D6 | ADR-012 revisado: `max(nominal + 15 min, Last-Modified + 5 min)` | Sí. Se descarta bajar a +5 min. |
| D7 | EIA: descargar ≈ 1.200 CSV del archivo (≈ 10 MB) para reconstruir la primera publicación | Sí. |
| D8 | Hora del WPSR en miércoles normales (no festivos), 2015–2026 | Aceptar 10:30 ET como hora estándar documentada (riesgo declarado) y validarla con un monitor prospectivo. R-PIT para semanas festivas y desplazadas. Alternativa estricta: R-PIT para todas (pierde la reacción intradía). |
| D9 | Regla R-PIT (§3) para el resto de horas no verificadas (COT 2015–2022, BLS/DOL, ALFRED) | Sí. Añadir al calendario COT los cierres federales verificados. |
| D10 | DTWEXBGS | Solo desde 2019-02-04. |
| D11 | NOAA | Fuera de las features; el clima se cubre con noticias. Auditoría NHC opcional más adelante. |
| D12 | Captura prospectiva (GDELT + hora real WPSR/COT) | Diseñarla en F2 con GitHub Actions. |

## 6. Procedencia y limitaciones conocidas
- **Commit "dirty":** los manifiestos de F1 registran el commit con sufijo `-dirty`, porque los scripts se commitearon después de descargar. Para F2 se propone que `RawStore` exija un árbol limpio.
- **Monitor GDELT:** sus observaciones (`data/raw/gdelt_lag_monitor/`) son un registro prospectivo en curso; se registrará con manifiesto al cerrarlo.
- **Revisión adversarial G0:** 11 hallazgos (1 crítico, 2 altos). El crítico era la contradicción D8/D9, que filtraba datos: se resolvió con R-PIT. Los altos se resolvieron con más evidencia (CFTC, GDELT) o quedan como decisiones. Las cifras erróneas (p95, porcentajes, huecos) están corregidas en `reports/f1/gdelt.md`.
