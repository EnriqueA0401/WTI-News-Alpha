# Plan F7 — Modelos M0–M4 en walk-forward (prueba principal de H1)

**Entrada:** `feat-v1` + `label-v1`, solo periodo de investigación (`models.data.load_research_frame`).
**Pre-registro:** `preregistration/H1.yaml`, congelado (ADR-044). El script se niega a correr si no está commiteado, sin cambios y con `status: approved`.

## Modelos (Documento §9; canónica la numeración de la sección 9)
| ID | Aprendiz | Grupos de features |
|---|---|---|
| M0 | climatología del train (frecuencias de clase) | ninguno |
| M1 | logística multinomial L2 (C = 1), fija | `mkt` |
| M2 | XGBoost (hiperparámetros fijos de F6) | `mkt`, `cal` |
| M3 | XGBoost | M2 + `wpsr`, `cot`, `fred` |
| M4 | XGBoost | M3 + `news` |

- **M1:** imputación por mediana del train + indicadores de faltante + escalado con media/desviación del train, todo dentro de cada fold (función nombrada que registra cuántas celdas imputa). Un aviso de no convergencia es error.
- **Prueba principal (pre-registrada):** M3 frente a M4, a 15 y 60 min, Holm sobre las 2. M1 y M2 son ablaciones descriptivas, fuera de la prueba principal.

## Salidas
- Predicciones OOS de todas las variantes en `data/runs/f7/` (no van a git; su sha256 va en el informe).
- `reports/f7/h1_primary.json`: pérdidas por modelo y horizonte, escalera de ablación, prueba M3/M4 con Holm, d por fold, dejar-un-trimestre-fuera y veredicto de cada criterio.
- Filas en `runs/registry.csv` (todas, incluidas las fallidas).

## Tests (primero)
- Holm: casos a mano (orden, monotonía, tope en 1).
- Dejar-un-grupo-fuera: caso a mano.
- Guardia: pre-registro en borrador o sin commitear → error; aprobado → sha256.
- M1: imputación y escalado ajustados solo con el train (caso a mano); columnas sin datos en el train → error; aviso de no convergencia → error.

## Hecho
`make check` en verde; corrida completa reproducible (mismo sha256 de predicciones al repetir); resultados por fold registrados.
