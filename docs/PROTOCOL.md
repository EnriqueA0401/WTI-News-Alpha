# Protocolo de investigación — WTI News Alpha

**Estado: APROBADO por el usuario el 2026-10-15 (cierre de F0).**
Las decisiones de §3 quedan aprobadas. Los valores que dependen de fuentes externas (horario y settlement de CL, tick, comisión, spread) siguen `unverified` hasta verificarlos (ADR-005); los que se fijan en fases posteriores (N del roll, splits y ventana del test sellado en F6) siguen `proposed`.

## 1. Pregunta
¿La información de noticias y eventos disponible en el instante de decisión mejora, fuera de muestra y neta de costes, la predicción del rendimiento o riesgo de NYMEX CL frente a un modelo que solo conoce mercado + fundamentales publicados hasta ese instante? (Documento §1.1). Un NO-GO bien demostrado es un resultado válido.

## 2. Hipótesis (Documento §1.2)
| ID | Hipótesis | Prueba |
|---|---|---|
| H1 | Valor incremental: features estructuradas de noticias mejoran la métrica primaria OOS frente al mejor modelo market + fundamentales. | **M4 vs M3** (Documento §9; numeración canónica, ADR-001), mismos folds y observaciones. |
| H2 | Heterogeneidad: el valor depende de la categoría de evento, novedad, credibilidad y régimen de volatilidad. | Desglose por categoría con corrección de multiplicidad (Holm/BH). |
| H3 | Persistencia: algunas categorías continúan, otras revierten. | Dirección inicial y duración medidas por separado (horizontes de sesión). |
| H4 | Utilidad operativa: la mejora sobrevive a retardos, precios ejecutables y costes. | Simulación F9 con sensibilidad 1×/2×/3×. |

## 3. Decisiones aprobadas
| # | Tema | Propuesta | Alternativa |
|---|---|---|---|
| A2 | Base temporal de horizontes | **Tiempo de mercado**: N minutos de sesión abierta. Etiqueta inválida (no rellenada) si cruza pausa diaria, cierre de fin de semana, festivo o roll. | Tiempo de reloj. |
| A3 | "Fin de sesión" | **Settlement NYMEX (14:30 ET, a verificar)**: precio de referencia líquido. | Cierre Globex 17:00 ET. |
| A4 | `decision_time` | Modelos: rejilla fija cada 5 min dentro de sesión. Estudio de eventos: t0 = `first_available_at` del evento. | Solo en eventos. |
| A5 | Rollover | Regla de calendario fija: N días hábiles antes del último día de negociación del contrato front; N medido en F1 (volumen/OI), usa solo información pasada. | Roll por volumen del día anterior. |
| A6 | Barras | `ohlcv-1m` agregadas a 5 min (sujeto a quote aprobado). | Solo 5 min. |
| A7 | Ventana / test sellado | Piloto 1–2 años dentro de 2015–2026 (GDELT 2.0 empieza feb-2015). Test sellado = último bloque ≈15–20 %, fijado y congelado en F6 antes de modelar. | — |
| A8 | Métrica primaria | Log loss de dirección 3 clases a **60 min**; secundarias Brier, balanced accuracy, macro-F1. Regla de trading: retorno neto con drawdown. Se fija definitivamente en F6 con pre-registro. | Brier como primaria. |
| A9 | Disponibilidad GDELT histórica | `first_available_at` = timestamp del fichero de 15 min + retraso conservador configurable (inicial 15 min), validado en F1. | — |
| A10 | Texto | Sin scraping masivo: títulos, URL, temas/entidades GKG y DOC API respetando límites. Texto completo solo donde los términos lo permitan. | — |
| A11 | NOAA | Solo contexto retrospectivo, marcado "sin disponibilidad real", salvo que exista hora de publicación verificable. | Excluir. |
| A12 | Sorpresa EIA | Sin consenso archivado: expectativa ingenua calculada solo con pasado; limitación documentada. | — |
| — | Costes caso base | ≥ 1 tick por lado de slippage + comisión y spread verificados; sensibilidad 1×/2×/3×. | — |
| — | Latencia de ejecución | Entrada al primer precio ejecutable tras `decision_time + latencia`; latencia inicial a definir (propuesta: 1 barra de 1 min). | — |

## 4. Sesiones CL (a verificar contra CME — ADR-005)
Valores de trabajo (no utilizables hasta verificar): apertura domingo 18:00 ET; pausa diaria 17:00–18:00 ET; cierre viernes 17:00 ET; settlement 14:30 ET; tick 0,01 USD/bbl = 10 USD/contrato; 1.000 bbl.

## 5. Reglas de ejecución (resumen; detalle en CLAUDE.md §3 y §7)
- Ningún dato con `available_at > decision_time`; todo ajuste solo con el train de cada fold.
- Releases (EIA WPSR, COT, FRED/ALFRED) activos desde su hora real de publicación, con excepciones verificadas en la fuente oficial.
- Ningún retorno ni etiqueta cruza contratos; MFE/MAE con supuesto adverso si stop y objetivo se tocan en la misma barra.
- Walk-forward cronológico con purga por intervalo de etiqueta + embargo, agrupación por evento.
- Test sellado: una sola apertura, tras pre-registro aprobado.
