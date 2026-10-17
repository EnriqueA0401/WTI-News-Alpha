# F7 — Modelos M0–M4: prueba principal de H1

**Corrida oficial:** commit `2edf449`, etiqueta `20261017T174355Z`, pre-registro `H1.yaml` congelado (sha256 `fd5adf62…`, ADR-044). Datos: `feat-v1` + `label-v1`, solo el periodo de investigación (2018–2020 y 2024–jun 2025). 14 folds trimestrales (primer test 2019-01-01), sin folds omitidos. 1.015 días de test.
**Reproducibilidad:** una segunda corrida independiente da el mismo sha256 de las predicciones OOS en los 5 modelos y los 2 horizontes.
**Detalle:** `reports/f7/h1_primary.json`. Predicciones en `data/runs/f7/` (fuera de git). Corridas en `runs/registry.csv`.

## Resultado principal (pre-registrado): M4 frente a M3

d = pérdida(M3) − pérdida(M4); un valor positivo significa que las noticias ayudan.

| Horizonte | d medio diario | IC 95 % (bootstrap por bloques de días) | p DM | p Holm | Folds con d > 0 | Pasa |
|---|---|---|---|---|---|---|
| 15 min | −0,00007 | [−0,00016; +0,00002] | 0,92 | 0,92 | 5/14 (36 %) | No |
| 60 min | +0,00009 | [−0,00033; +0,00052] | 0,34 | 0,69 | 6/14 (43 %) | No |

- **Ningún horizonte cumple ninguno de los 4 criterios.**
- **H1 no se sostiene:** con este dataset, las noticias estructuradas no reducen la log loss frente a M3.
- Escala: la log loss de M3 es ≈ 1,09. Un efecto de 0,0001 es menos del 0,01 % de esa pérdida.
- El resultado es coherente con el placebo de F6, donde M4 con noticias de otros días ya empataba con M3.

## Escalera de ablación (descriptiva, fuera de la prueba principal)

Log loss OOS media (3 clases; la climatología vale ≈ ln 3 = 1,0986):

| Modelo | 15 min | 60 min |
|---|---|---|
| M0 climatología | 1,09860 | 1,09854 |
| M1 logística (mercado) | 1,10184 | 1,10435 |
| M2 XGB mercado + calendario | 1,09054 | 1,08407 |
| M3 + fundamentales | 1,09048 | 1,08524 |
| M4 + noticias | 1,09053 | 1,08503 |

- **El mercado sí predice algo:** M2 supera claramente a M0 (p < 1e-30 en los dos horizontes).
- **Los fundamentales no añaden nada:** M2 frente a M3, p ≈ 0,45. A 60 min, M3 queda incluso algo peor que M2.
- **M1 queda peor que la climatología.** Se rompe sobre todo en 2020-T1 (COVID; pérdida 1,15–1,19): la logística extrapola mal en un régimen extremo. Es solo una referencia; no forma parte de la prueba.

## Revisión adversarial (ADR-045)
- No hay hallazgos altos. Se corrigieron tres medios: el control de NaN en Holm, el registro de corridas fallidas y los tests de la lógica del veredicto.
- **Riesgo aceptado:** la feature booleana `news_source_ok` no entra en M4. No se cambia tras congelar el pre-registro; su información ya está en los nulos de las demás features de noticias.

## Qué no dice este resultado
- No descarta que las noticias tengan valor con otra fuente, por ejemplo texto completo o un feed de baja latencia en lugar de GDELT.
- No lo descarta tampoco a otros horizontes ni con otra representación (embeddings, M5).
- Solo dice que **no hay evidencia incremental con este dataset y esta especificación**.
