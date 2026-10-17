# Puerta G2 — Calidad de la extracción de eventos (`rules-v2` / `events-v2`)

Fecha: 2026-10-17. Script: `scripts/f3/g2_metrics.py` (semilla 20261017). Cifras: `reports/f3/g2_metrics.json`. Etiquetas: `reports/f3/g2_labels.csv`.

## Cómo se midió
- **Muestra:** 288 titulares (`scripts/f3/g2_sample.py`), de los cuales 262 son originales y 26 son repeticiones.
  - 182 que las reglas marcan como directos: 14 por familia, la mitad con título real y la otra mitad con titular sacado de la URL;
  - 45 indirectos;
  - 35 negativos difíciles (titulares con oil, gas, fuel… que las reglas marcan como no relevantes).
- **Etiquetas de referencia:** las de Claude, leyendo cada titular sin mirar las reglas. El usuario las aceptó sin revisarlas una a una (ADR-031). **No es una auditoría humana independiente**, y la consistencia del etiquetador no se pudo medir.
- **Ponderación:** la muestra tiene el mismo tamaño en cada familia, pero la población no. Las cifras globales se ponderan por el tamaño real de cada estrato en `events-v2` (601.899 artículos directos). El intervalo es un bootstrap estratificado al 95 %.

## Resultados globales (ponderados)
| Medida | Todas las familias | Solo familias que generan eventos |
|---|---|---|
| Lo que las reglas llaman "directo" lo es | **54 %** [45–61] | **71 %** [65–78] |
| Además, la familia es correcta | **32 %** [26–39] | **55 %** [48–62] |

- La familia `other` es el 48 % de los artículos directos y solo el 36 % lo son de verdad. No genera eventos, pero sí infla el volumen de artículos directos.
- **Título real frente a titular de la URL:** no hay diferencia clara.
  - Directo: 70 % frente a 65 %.
  - Familia correcta: 44 % frente a 43 % (sin ponderar).
- **Mecanismo (dirección):**
  - las reglas dejan el 73 % como "ambiguo";
  - cuando eligen una dirección, acierta solo en **17 de 50 (34 %** [22–48]).
  - Parte de ese desacuerdo se debe a que Claude marcó "ambiguo" con frecuencia.
- **Fuera de los directos:**
  - de los 35 negativos difíciles, ninguno era directo;
  - de los 45 indirectos, 3 eran directos y 29 no relevantes.
  - Las reglas pierden pocos directos en esos estratos, pero la exhaustividad total no se midió.

## Por familia (sin ponderar; n = 14 por familia; intervalo de Wilson al 95 %)
| Familia | % de directos | Directo | Familia correcta | Mecanismo correcto (cuando no es ambiguo) |
|---|---|---|---|---|
| opec_policy | 10,3 | 13/14 [69–99] | 13/14 [69–99] | 2/5 [12–77] |
| eia_inventories | 2,9 | 14/14 [78–100] | 9/14 [39–84] | 2/5 [12–77] |
| sanctions_geopolitics | 3,8 | 13/14 [69–99] | 9/14 [39–84] | 3/14 [8–48] |
| weather | 0,4 | 13/14 [69–99] | 8/14 [33–79] | 4/7 [25–84] |
| conflict_attack | 6,4 | 10/14 [45–88] | 9/14 [39–84] | 2/7 [8–64] |
| logistics | 6,1 | 10/14 [45–88] | 7/14 [27–73] | 2/2 [34–100] |
| demand_macro | 5,5 | 10/14 [45–88] | 6/14 [21–67] | — |
| market_report* | 5,7 | 12/14 [60–96] | 8/14 [33–79] | 0/1 |
| supply_disruption | 3,3 | 9/14 [39–84] | 3/14 [8–48] | 1/6 [3–56] |
| rates_usd | 1,6 | 7/14 [27–73] | 4/14 [12–55] | 1/1 |
| opinion* | 0,4 | 6/14 [21–67] | 2/14 [4–40] | 0/1 |
| other* | 48,3 | 5/14 [16–61] | 1/14 [1–31] | 0/1 |
| company | 5,4 | 1/14 [1–31] | 0/14 [0–22] | — |

\* No generan eventos (`families_without_events`).

## Lectura
- **Aceptables:** `opec_policy`, y en menor medida `eia_inventories`, `sanctions_geopolitics`, `weather` y `conflict_attack`. Tienen relevancia alta, pero la familia solo es correcta en 9 de cada 14.
- **Malas:** `company` (casi todo ruido), `supply_disruption` (la relevancia vale, pero la familia falla), `rates_usd`, y `demand_macro` en la familia.
- **Mecanismo:** las reglas no sirven para dar dirección. No debe usarse como señal en M4 tal como está.
- **Trazabilidad:** cada artículo tiene su `article_id`, el fichero GKG de origen, la URL y la regla que lo clasificó (`*_rule`). Se cumple.
- **Los intervalos son anchos** (n = 14 por familia). Las diferencias entre familias del bloque intermedio no son concluyentes.

## Riesgos
- Las etiquetas de referencia son de Claude, no de una persona (ADR-031).
- Si las reglas se ajustan con esta misma muestra, las cifras quedan optimistas. Hay que volver a medir con una muestra nueva.

## Decisión requerida (puerta G2)
Según el Documento (§11, G2), si la precisión no es razonable: simplificar la ontología, corregir la extracción y volver a evaluar.
1. **Simplificar y volver a medir (recomendado).**
   - Con `rules-v3`, solo generan eventos las familias aceptables: OPEP+, inventarios EIA, sanciones/geopolítica, clima y conflicto. Las demás pasan a "sin evento".
   - La dirección no se usa como señal hasta tener un extractor mejor.
   - Después se mide con una muestra nueva de unas 150 noticias.
2. **Corregir las reglas de todas las familias** y volver a medir. Es más trabajo, y la ganancia es incierta en `company` y `other`.
3. **Aprobar G2 tal cual.** No lo recomiendo: casi la mitad de los artículos que generan eventos tienen la familia mal.
