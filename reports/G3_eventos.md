# Puerta G3 — Estudio de eventos (F5)

Fecha: 2026-10-17. Script: `scripts/f5/event_study.py` (commit 96ee6d0; semilla 20261017). Tablas: `reports/f5/` (`event_study_summary.csv`, `segments.csv`, `coverage.csv`, `extremes.csv`, `summary.json`).

**Solo periodo de investigación:** eventos con t0 < 2025-06-24 y barras < 2025-07-01 (ADR-036). `check_research_only` lo comprueba; el periodo sellado no se ha tocado.

## Diseño (FASE_05.md, ADR-037)
- **Eventos:** `events-v3`, 5 familias, con t0 = primera disponibilidad accionable.
  - 15.144 eventos en el periodo; 12.310 (81 %) tienen entrada. El resto cae con el mercado cerrado.
- **Entrada:** cierre bid de la primera barra completa ≥ t0 + 1 min.
- **Retornos:** a 5, 15, 30 y 60 min, a fin de sesión (17:00 ET) y a 1 y 3 sesiones. Si cruzan un hueco o la pausa, son nulos.
- **Comparación** con 316.957 instantes de la rejilla sin evento de la misma familia en ±4 h:
  - **hora:** mismo día de la semana, hora ET y año;
  - **hora + volatilidad:** lo mismo y el mismo tercil de la volatilidad de los 60 min previos (terciles de 2018–2020).
- **Placebo:** 500 sorteos de un instante comparable por evento. Se compara la mediana de |retorno|.
- **Incertidumbre:**
  - bootstrap por bloques de un día para la mediana de los eventos;
  - q-valores de Benjamini-Hochberg sobre 35 pruebas por variante.

## Resultado principal (emparejado por hora y volatilidad)
Mediana de |retorno| en puntos básicos, con IC 95 %, para los eventos frente a los placebos comparables:

| Familia | Horizonte | n | Eventos | Placebo | Razón | q (BH) |
|---|---|---|---|---|---|---|
| opec_policy | 5 min | 5.530 | 6,09 [5,73–6,45] | 5,66 [5,43–5,92] | **1,08** | **0,035** |
| opec_policy | 15 min | 5.509 | 10,64 [10,06–11,11] | 10,28 [9,93–10,64] | 1,04 | 0,17 |
| opec_policy | 60 min | 5.296 | 22,31 [21,03–23,70] | 21,96 [21,22–22,78] | 1,02 | 0,38 |
| opec_policy | fin de sesión | 4.879 | 76,63 [70,87–82,38] | 70,49 [68,08–72,76] | **1,09** | **0,035** |
| conflict_attack | 5 min | 2.131 | 5,71 [5,35–6,23] | 5,37 [5,12–5,67] | 1,06 | 0,08 |
| conflict_attack | 60 min | 2.025 | 21,78 [20,00–23,44] | 21,16 [19,97–22,52] | 1,03 | 0,42 |
| sanctions_geopolitics | 5 min | 2.327 | 5,34 [5,02–5,72] | 5,00 [4,77–5,24] | 1,07 | 0,053 |
| eia_inventories | 5 min | 1.898 | 5,52 [5,10–6,09] | 5,33 [5,03–5,66] | 1,04 | 0,38 |
| eia_inventories | 60 min | 1.779 | 21,04 [19,41–22,76] | 20,88 [19,82–22,12] | 1,01 | 0,67 |
| weather | 5 min | 415 | 6,95 [5,77–7,64] | 5,49 [4,82–6,39] | **1,26** | **0,047** |
| weather | 60 min | 388 | 23,68 [19,54–27,47] | 21,55 [18,78–24,30] | 1,10 | 0,24 |

Todas las filas, incluidas las de 30 min y 1 y 3 sesiones, están en `event_study_summary.csv`. A 1 y 3 sesiones ninguna familia supera al placebo: las razones van de 0,91 a 1,07.

## Lectura
1. **Hay una reacción pequeña y rápida tras las noticias:**
   - OPEP+ y clima a 5 min, y OPEP+ a fin de sesión, superan al placebo tras corregir por pruebas múltiples;
   - conflicto y sanciones quedan al límite (q ≈ 0,05–0,08).
   - El exceso es de un 6–9 % (≈ 0,4 pb a 5 min). En clima es del 26 %, pero con 415 eventos.
2. **Buena parte del efecto bruto era volatilidad previa.**
   - Emparejando solo por hora, 10 de 35 pruebas salían significativas.
   - Al exigir además la misma volatilidad previa quedan 3. Las noticias llegan en momentos ya agitados.
3. **Inventarios EIA:** sin efecto frente a otros miércoles comparables. El mercado reacciona al dato de las 10:30 en cualquier caso; la noticia no añade nada medible.
4. **Dirección:** la mediana del retorno con signo es ≈ 0 en todas las familias y horizontes intradía. Sin el mecanismo (ADR-032) no hay dirección observable.
5. **A 1–3 sesiones no hay nada** distinguible del placebo.

## Diversidad y calidad de datos (criterio G3)
- **Cobertura:**
  - 2.600–2.950 eventos con retorno por año en 2018, 2019, 2020 y 2024, y 1.207 en el primer semestre de 2025;
  - las 5 familias están presentes cada año, `weather` con 23–159 eventos al año.
- **Época:** el |retorno| a 15 min es mayor en la época con título real (p. ej. OPEP+: 12,5 frente a 8,4 pb). Coincide con años de distinta volatilidad; ya está previsto (ADR-034).
- **Volatilidad:** el |retorno| a 15 min va de ~5,5 pb (tercil bajo) a ~20 pb (alto) en todas las familias. Domina sobre la familia.
- **Episodios extremos:** los mayores movimientos son de marzo–abril de 2020 (WTI negativo). Las medianas no dependen de ellos, pero F8 hará el análisis dejando fuera ese episodio.
- **Problemas encontrados:**
  1. Un mismo titular puede abrir eventos en varios países a la vez (mismo t0, p. ej. IR y AE el 2020-04-21). No son independientes; por eso el bootstrap es por días.
  2. El 19 % de los eventos no tiene entrada (mercado cerrado).
  - No se han visto errores de timestamps ni precios imposibles.

## Riesgos
- Las etiquetas de familia tienen ~30 % de error (G2).
- El precio es un CFD bid, no el futuro CL.
- El placebo controla la hora y la volatilidad, pero no otros eventos de otras familias cercanos.
- 500 sorteos dan un p mínimo de 0,002.

## Decisión requerida (puerta G3)
- **Aprobar G3 y pasar a F6 (recomendado):**
  - etiquetas, splits, costes y controles sintéticos;
  - expectativa realista: si las noticias aportan algo, será poco y en horizontes cortos (≤ 15 min) y en volatilidad más que en dirección.
- **No aprobar:** ampliar el estudio (otras definiciones de evento o más placebos) antes de seguir.
