# Plan F6 — Etiquetas, splits, costes, guardia del sellado y controles sintéticos

**Puerta G4:** todo es reproducible y los controles positivo, negativo y placebo se comportan como se espera (CLAUDE.md §8.5).
**Datos:** solo el periodo de investigación (ADR-036). El sellado solo existe a través de la guardia.

## Componentes (críticos, CLAUDE.md §7)
1. **Etiquetas** (`labels/`), por `decision_time` y horizonte:
   - entrada: primera barra completa ≥ t + 1 min (ADR-037); salida: última barra ≤ entrada + h;
   - nula (sin rellenar) si cruza la pausa diaria, el fin de semana o un hueco > 5 min;
   - retorno log neto y bruto; MFE/MAE, con lo adverso primero si stop y objetivo caen en la misma barra;
   - **3 clases:** alcista / neutral / bajista si |r| supera k × volatilidad previa. k se ajusta **solo con el train** de cada fold;
   - versión `label-v1` publicada con el mismo sistema de manifiestos.
2. **Splitter walk-forward:**
   - folds cronológicos con **purga** por intervalo de etiqueta y **embargo**;
   - agrupación por evento: artículos del mismo evento nunca en train y test a la vez;
   - test de propiedad: ningún intervalo de etiqueta del train solapa el test.
3. **Modelo de costes:** comisión, spread (`avg_spread` real del CFD, o ≥ 1 tick), slippage ≥ 1 tick por lado y latencia, con sensibilidad 1×/2×/3× (`config/costs.yaml`).
4. **Guardia del test sellado:**
   - rechaza cualquier lectura del periodo sellado si no hay pre-registro commiteado y flag explícito;
   - registra cada acceso en un log append-only;
   - un segundo acceso queda registrado y se reporta.
5. **Controles sintéticos del pipeline completo** (solo en `tests/` y en un directorio sintético marcado):
   - **positivo:** señal plantada en un tipo de evento → la línea M4 la detecta y M3 no;
   - **negativo:** sin señal → ningún modelo supera al baseline más allá del ruido;
   - **canario:** la etiqueta futura como feature → la guardia la bloquea; si se fuerza, la métrica es casi perfecta.

## Decisiones del usuario (CLAUDE.md §4) — se piden antes de implementar
- Horizonte principal.
- Objetivo y métrica primaria.
- Esquema de validación y embargo.

## Tests (primero)
- Etiquetas: casos a mano por horizonte, hueco, pausa, fin de semana y MFE/MAE con empate.
- Umbral ajustado solo en train.
- Splitter: propiedades con `hypothesis`.
- Costes: casos a mano.
- Guardia: acceso sin pre-registro → error; segundo acceso registrado.
- Determinismo (hash).
