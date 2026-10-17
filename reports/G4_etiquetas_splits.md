# Puerta G4 — Etiquetas, splits, costes, guardia y controles (F6)

Fecha: 2026-10-17. Decisiones: ADR-040 (diseño), ADR-041 (costes), ADR-042 (dependencias), ADR-043 (revisión adversarial).

## Qué está hecho
| Componente | Estado | Evidencia |
|---|---|---|
| Etiquetas `label-v1` | Publicada | 318.340 instantes de 2018-01 a 2025-06, solo investigación. Válidas: 15 min 97,8 %, 60 min 92,7 %, 4 h 76 %, fin de sesión 86 %, 1 sesión 94 % y 3 sesiones 93 %. Ninguna etiqueta toca el sellado (comprobado en la revisión). |
| Splitter | Hecho | Walk-forward trimestral con ventana creciente, purga por intervalo de etiqueta y 1 día de embargo. Test de propiedad: ningún intervalo del train toca el test. 14 folds (2019T1–2020T4 y 2024T1–2025T2). |
| Costes | Hecho | Spread real del CFD, más 1 tick de deslizamiento por lado y comisión 0 (no verificada). Sensibilidad 1×/2×/3×, con casos a mano. |
| Guardia del sellado | Hecha y conectada | Sin pre-registro commiteado y flag explícito no se abre. Log encadenado por hash. Detecta el borrado, la edición y el truncado del log. La línea de modelos falla ante cualquier fila sellada. |
| Línea M0/M3/M4 | Hecha | XGBoost regularizado con hiperparámetros fijos. Solo features de la capa publicada. Los folds se validan contra los datos. |
| Comparación | Hecha | DM con HAC y corrección HLN, bootstrap de bloques móviles y mínimo de 30 días. Tamaño medido < 12 % bajo nulo autocorrelado. |
| Revisión adversarial | Resuelta (ADR-043) | 1 hallazgo alto, 6 medios y 3 bajos corregidos; MFE/MAE y retorno neto se aplazan a F9 (`label-v2`). |
| Tests | 643, en verde | Cobertura de ramas del 99 %. |

## Controles sintéticos (CLAUDE.md §8.5; `tests/test_synthetic_controls.py`, datos sintéticos)
| Control | Esperado | Resultado |
|---|---|---|
| **Positivo:** señal de 0,5 desviaciones típicas solo con noticias de OPEP+; otra categoría es ruido | M4 > M3 y M3 no mejora a M0 | M4 mejora (p_DM < 0,01, bootstrap < 0,01); M3 frente a M0 no es significativo. ✔ |
| **Negativo:** sin señal | Nadie mejora | M4 frente a M3: p_DM > 0,01, diferencia < 0,005; M4 frente a M0, no significativo. ✔ |
| **Canario:** el `z` futuro como feature | La guardia bloquea; si se fuerza, la pérdida es casi perfecta | `LeakageError`; forzado, log loss < 0,1 (uniforme = 1,099). ✔ |
| Una columna de etiqueta con prefijo de grupo | Nunca entra como feature | Excluida. ✔ |

## Placebo con datos reales (CLAUDE.md §8.6; `scripts/f6/real_placebo.py`, `reports/f6/placebo.json`)
Las noticias de cada día se sustituyen por las de un día anterior con el mismo día de la semana (1–8 semanas antes). Se mantiene la estructura de las noticias, pero desaparece su relación con el precio. **M4 no se ha ejecutado con las noticias reales.**

| Horizonte | Test (filas / días) | Log loss M0 | M3 | M4 placebo | M3 − placebo (por día) | p_DM |
|---|---|---|---|---|---|---|
| 15 min | 227.949 / 1.015 | 1,0986 | 1,0905 | 1,0905 | −0,00003 [−0,00017; 0,00011] | 0,65 |
| 60 min | 218.168 / 1.015 | 1,0985 | 1,0852 | 1,0849 | −0,00023 [−0,00081; 0,00032] | 0,78 |

- **El placebo no mejora a M3:** se comporta como se espera.
- **M3 mejora a la climatología** (M0 − M3: 0,0075 a 15 min y 0,0134 a 60 min; p < 10⁻³⁸). La línea extrae información real del mercado y los fundamentales.
  - Sobre todo es la volatilidad (ADR-034): el `z` se escala con la volatilidad previa, pero la forma de la distribución cambia con ella.
- Todas las corridas están en `runs/registry.csv`.

## Riesgos y límites
- Comisión del bróker no verificada (caso base optimista).
- CFD bid, no el futuro CL.
- MFE/MAE y retorno neto en `label-v2` (F9).
- Con empates, el umbral de 3 clases deja algo más de 1/3 en neutral (despreciable con n grande).

## Decisiones requeridas
1. **Aprobar G4.**
2. **Aprobar y congelar el pre-registro `preregistration/H1.yaml`:**
   - horizontes de 15 y 60 min (15 min declarado como informado por los datos), con Holm;
   - criterios numéricos de aceptación;
   - hiperparámetros fijos de `models/pipeline.py`.

   Una vez aprobado, F7 ejecuta M4 con las noticias reales y el resultado no se toca.
