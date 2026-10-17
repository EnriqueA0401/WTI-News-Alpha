# Puerta G3 — Estudio de eventos (F5)

Fecha: 2026-10-17. Script: `scripts/f5/event_study.py` (commit 3700929, árbol limpio; `summary_sha256` 57f03a08…, idéntico en dos ejecuciones). Método: ADR-038. Tablas: `reports/f5/`.

**Solo periodo de investigación:** eventos con t0 < 2025-06-24 y barras < 2025-07-01. El periodo sellado no se ha tocado; lo confirmó también la revisión adversarial.

## Diseño
- **Eventos:** `events-v3`, 5 familias, con t0 = primera disponibilidad accionable.
  - 15.144 eventos; 12.310 tienen entrada (el resto cae con el mercado cerrado).
  - Entrada: cierre bid de la primera barra ≥ t0 + 1 min.
- **Medidas:** |retorno| a 5, 15, 30 y 60 min, a fin de sesión (17:00 ET) y a 1 y 3 sesiones. Si la ventana cruza un hueco o la pausa, la medida es nula.
- **Comparación:**
  - cada evento se sitúa en el **percentil** de su |retorno| entre los instantes comparables;
  - comparables: mismo día de la semana, hora, **minuto** ET y año, sin evento de su familia en ±4 h;
  - variante `time_vol`: además, el mismo tercil de volatilidad de los 60 min previos.
  - Si las noticias no añaden movimiento, la media de los percentiles es **0,50**.
- **Incertidumbre:** bootstrap por días (intradía y fin de sesión) o por semanas (1–3 sesiones), con BH sobre las 35 pruebas de cada variante.
  - Calibrado con eventos falsos agrupados como los reales: ≤ 5 % de falsos positivos.

## Resultado
Media del percentil de |retorno| (0,50 = igual que un momento comparable), con IC 95 %; p unilateral y q de BH.

| Familia | Horizonte | n | Solo momento | q | Momento + volatilidad | q |
|---|---|---|---|---|---|---|
| opec_policy | 5 min | 5.445 / 4.779 | **0,521** [0,509–0,533] | **0,035** | 0,511 [0,498–0,523] | 0,34 |
| opec_policy | 30 min | 5.350 / 4.712 | **0,518** [0,506–0,530] | **0,035** | 0,510 [0,498–0,523] | 0,34 |
| opec_policy | 60 min | 5.217 / 4.601 | 0,511 [0,499–0,523] | 0,17 | 0,499 [0,486–0,511] | 0,93 |
| conflict_attack | 30 min | 2.064 / 1.916 | **0,524** [0,508–0,541] | **0,035** | 0,516 [0,500–0,532] | 0,34 |
| conflict_attack | 60 min | 2.008 / 1.868 | 0,509 [0,492–0,525] | 0,40 | 0,502 [0,484–0,520] | 0,74 |
| weather | 5 min | 413 / 380 | 0,540 [0,509–0,572] | 0,084 | 0,540 [0,508–0,574] | 0,18 |
| weather | 30 min | 399 / 368 | **0,549** [0,513–0,582] | **0,035** | 0,551 [0,518–0,584] | 0,14 |
| sanctions_geopolitics | 5 min | 2.309 / 2.158 | 0,504 [0,490–0,519] | 0,53 | 0,502 [0,487–0,518] | 0,74 |
| eia_inventories | 5 min | 1.865 / 1.711 | 0,505 [0,489–0,521] | 0,53 | 0,512 [0,496–0,529] | 0,34 |

El resto (fin de sesión y 1–3 sesiones) está en `event_study_summary.csv`. Ninguna medida pasa de q = 0,16 en esos horizontes; las medias van de 0,47 a 0,53.

## Lectura
1. **Emparejando solo por momento** hay una reacción pequeña:
   - OPEP+ (5 y 30 min), conflicto (30 min) y clima (30 min) superan el control tras BH;
   - el tamaño es modesto: el evento típico queda en el percentil 52–55 en lugar del 50.
2. **Al exigir además la misma volatilidad previa**, nada sobrevive a la corrección (q mínimo 0,14).
   - Clima a 5 y 30 min tiene un p sin corregir de 0,01 y 0,004, con solo ~370 eventos.
   - Lectura: las noticias de GDELT llegan sobre todo en momentos ya agitados. Puede que el mercado reaccione **antes** de que el artículo esté disponible (GDELT llega con 15 min o más de retraso). Es una hipótesis: no se ha medido aquí.
3. **No hay dirección:** la mediana del retorno con signo es ≈ 0 intradía, y el mecanismo no se usa (ADR-032).
4. **A 1–3 sesiones no hay nada:** ni en la variante de momento ni en la de volatilidad hay q < 0,16.
5. **Inventarios EIA:** sin diferencia frente a instantes comparables. Los eventos de esta familia no coinciden con el dato de las 10:30: solo el 2 % de los del miércoles cae entre las 10:30 y las 11:00 ET. Con este diseño no se puede decir nada del efecto del dato oficial.

## Diversidad y calidad de datos (criterio G3)
- **Cobertura:**
  - 2.600–2.950 eventos con retorno por año en 2018, 2019, 2020 y 2024, y 1.207 en el primer semestre de 2025;
  - las 5 familias están presentes cada año; `weather` tiene 23–159 eventos al año, con intervalos anchos.
- **La volatilidad domina:** el |retorno| a 15 min va de ~5,5 pb (tercil bajo) a ~20 pb (alto) en todas las familias (`segments.csv`).
- **Época:** el |retorno| es mayor en la época con título real, que coincide con años de distinta volatilidad (ADR-034).
- **Extremos:** son de marzo–abril de 2020 (WTI negativo) y son barras reales. F8 hará el análisis dejando fuera ese episodio.
- **Problemas detectados:**
  1. un titular puede abrir eventos en varios países a la vez (2–10 % de los eventos);
  2. el 19 % de los eventos cae con el mercado cerrado;
  3. los eventos solo llegan a los cuartos de hora, y el emparejamiento ya lo tiene en cuenta.

## Riesgos y límites
- Familias con ~30 % de error de etiqueta (G2); CFD bid, no el futuro CL.
- El control no ajusta por eventos cercanos de otras familias.
- Análisis del plan no hechos, que se aplazan (ADR-038): RV posterior frente a previa, tiempos hasta el umbral y la retracción, segmentos por novedad, fuentes y sesión, y control de baja relevancia.
- Se han hecho 70 pruebas más los segmentos; BH es por variante.

## Decisión requerida (puerta G3)
- **Aprobar G3 y pasar a F6 (recomendado).** Hay datos y diversidad suficientes, la incertidumbre es explícita y no hay errores de datos abiertos. La evidencia preliminar es débil:
  - si M4 supera a M3, será por poco y en horizontes ≤ 30 min;
  - un NO-GO de señal es un resultado plausible y válido.
- **Ampliar F5 antes de seguir:** completar los análisis aplazados.
