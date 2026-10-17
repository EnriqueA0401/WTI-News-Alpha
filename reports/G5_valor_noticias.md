# Puerta G5 — Valor incremental de las noticias

**Criterio de la puerta (Documento §11):** M4 supera a M3 fuera de muestra en la métrica primaria pre-registrada, de forma estable y sin que un evento aislado domine.
**Veredicto propuesto: no se cumple.** No hay evidencia de valor incremental de las noticias con este dataset.

## 1. Prueba principal (pre-registrada, F7)
Detalle: `reports/F7_modelos.md`.
- M4 frente a M3, log loss en 3 clases, 14 folds trimestrales, 1.015 días de test, Holm sobre 2 pruebas.
- **15 min:** d = −0,00007, IC 95 % [−0,00016; +0,00002], p Holm 0,92.
- **60 min:** d = +0,00009, IC 95 % [−0,00033; +0,00052], p Holm 0,69.
- **Ningún horizonte cumple ninguno de los 4 criterios.** La corrida es reproducible: el sha256 de las predicciones es idéntico en dos corridas.

## 2. Robustez y análisis secundarios (F8)
Detalle: `reports/f8/robustness.json`. Revisión adversarial resuelta en ADR-046.

| Análisis | 15 min | 60 min |
|---|---|---|
| Brier M3 − M4 (calibración) | −0,00004 (p 0,92) | +0,00005 (p 0,36) |
| ECE por clase (M3 / M4) | ≈ 0,005–0,014, iguales | ≈ 0,008–0,018, iguales |
| Magnitud \|ret\| (pre-registrada) | M4 peor: −1,6 pb², q 0,96 | M4 peor: −2,7 pb², q 0,96 |
| Magnitud sin 20–21 abr 2020 (no pre-registrado) | −1,5 pb², p 0,96 | −1,0 pb², p 0,56 |
| SHAP de M4 (último fold): peso de las noticias | 0,9 % | 2,5 % |

**Subgrupos (42 pruebas, BH conjunta con p bilateral; cada día pesa igual):**
- **Por familia:** con un evento nuevo en la última hora (OPEP+, EIA, conflicto, sanciones, clima), ninguno sobrevive a BH. El menor q es 0,58 (OPEP+ a 60 min, p bilateral 0,065).
- **Resto de cortes:** con y sin noticias, fuente caída, terciles de volatilidad, tramos de sesión, año y época de titulares. **Ningún q < 0,50.**
- **Sin señal escondida:** ningún subgrupo muestra una ayuda que se compense con un daño en otro.
- **Descriptivos (fuera de BH):** los cortes por resultado (sube, baja, neutral) solo muestran que M4 reparte la confianza entre clases de forma algo distinta. No son predictivos.

**Dejar un evento fuera:** con efecto nulo no hay episodio que domine. En la magnitud, el error cuadrático sí lo dominan los días de precios negativos, pero quitarlos no cambia la conclusión.

## 3. Lectura
- **El mercado tiene algo de señal de dirección:** M2 bate a M0 con p < 1e-30.
- **Ni los fundamentales ni las noticias añaden nada medible** encima del mercado.
- El resultado coincide con el placebo de F6, donde las noticias de otros días ya rendían igual.
- **Límites de la conclusión:**
  - solo vale para GDELT como fuente de noticias, con su latencia (≥ 15 min);
  - las noticias se representan con la ontología de 5 familias;
  - los periodos son 2019–2020 y 2024–2025.
- **No descarta valor:**
  - con texto completo o un feed de baja latencia;
  - con otra representación (M5, embeddings);
  - en eventos raros que no aparecen en este periodo.

## 4. Opciones (Documento §11, "Si falla…")
1. **Cerrar con NO-GO de señal y pasar a F10** (recomendado).
   - El test sellado (jul-2025 → jul-2026) se abre una sola vez con el pre-registro H1 congelado, para confirmar el resultado con datos nunca vistos.
   - Después se escribe el informe final.
   - F9 (simulación económica) se reduce a declarar "sin valor predictivo que monetizar".
2. **Cerrar con NO-GO sin abrir el sellado.** Se reserva el periodo sellado para una hipótesis futura con mejores datos de noticias. El coste es quedarse sin confirmación independiente.
3. **Redefinir la hipótesis de forma registrada** (p. ej., H2 solo con OPEP+, o M5 con embeddings).
   - **No recomendado:** el CLAUDE.md §12 dice "no escales complejidad para compensar falta de señal".
   - Ningún subgrupo da una pista que lo justifique (menor q 0,58).
