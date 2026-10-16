# F3 — Noticias → eventos (relevancia, taxonomía, clustering online, muestra auditada)

**Estado:** G1 aprobada (ADR-027). Puerta de salida: **G2**, con precisión auditada por categoría y trazabilidad a fuentes.

## Entradas
- `data/normalized/gdelt_news/gdelt-news-v2/` (12,0 M filas). Campos útiles:
  - título (desde 2019/2020);
  - URL;
  - medio;
  - temas V1;
  - organizaciones, personas y lugares;
  - cantidades;
  - tono;
  - `first_available_at`.
- **No hay texto completo:** sin scraping masivo (A10). La extracción trabaja sobre título, URL y metadatos de GDELT.
- Documento §6.1–6.3 y §16.3: pasos, ontología y taxonomía.

## Pasos (Documento §6.1, fase 1 de §6.3: reglas y diccionarios)
1. **Limpieza y deduplicación online:**
   - URL canónica (sin parámetros de seguimiento, `amp`, `m.`) y hash;
   - duplicado exacto si la URL o el título normalizado del mismo medio aparecen antes;
   - solo se compara con artículos ya disponibles (`first_available_at` anterior).
2. **Relevancia WTI (reglas):** puntuación con diccionarios versionados (`config/events_rules.yaml`) sobre título, temas, organizaciones y URL. Tres clases: directamente relevante / contexto indirecto / no relevante.
3. **Familia del evento y mecanismo (reglas):** taxonomía del §16.3.
   - Familias: inventarios EIA, OPEP+, interrupción de producción, sanciones/geopolítica, conflicto/ataque, clima, logística, demanda, tipos/USD, empresa, opinión, otro.
   - Mecanismos: estrecha oferta, amplía oferta, aumenta demanda, reduce demanda, riesgo, ambiguo.
   - Cada fila guarda la regla que disparó la etiqueta (trazabilidad).
4. **Clustering online de eventos:**
   - un artículo se une al clúster abierto más parecido (misma familia, entidades o lugares, similitud de título) dentro de una ventana pasada;
   - si no hay ninguno, abre un evento nuevo;
   - las actualizaciones que cambian el hecho se marcan y no se colapsan.
   - **Test de truncamiento obligatorio:** el clustering en t es idéntico con datos completos o truncados en t.
5. **Scoring:**
   - novedad (primer artículo del evento);
   - nº de fuentes distintas acumuladas hasta t;
   - intensidad.
   - No se inventan magnitudes físicas.
6. **Muestra auditada (G2):** guía de etiquetado y un piloto de 200–300 artículos estratificados por familia y año, con negativos difíciles. Se mide la precisión por categoría (con intervalo) frente a las reglas.

## Salidas
- `data/events/<version>/articles.parquet`: artículo + `canonical_url`, `dup_of`, relevancia, familia, mecanismo, regla y `event_id`.
- `data/events/<version>/events.parquet`: evento, `first_available_at` del primer artículo, artículos y fuentes acumuladas.
- `docs/labeling_guide.md`, la muestra para etiquetar y el informe G2.

## Invariantes críticos
- Ninguna decisión (duplicado, clúster, novedad) usa artículos con `first_available_at` posterior.
- Los diccionarios y umbrales están versionados; su ajuste en F7 se hará solo con datos de train.

## Tests
- Casos a mano para cada regla y para la deduplicación.
- Truncamiento con hypothesis.
- Canario: un artículo futuro no puede cambiar un clúster pasado.
- Determinismo.

## Riesgos
- Sin texto completo, la extracción es débil en 2018 (sin títulos).
- El sesgo de los diccionarios se mide con la muestra auditada.
- Un LLM para la extracción (fase 2 del §6.3) cuesta dinero: no se usa sin aprobación (CLAUDE.md §4).

## Decisión pendiente del usuario
- Quién etiqueta la muestra de G2: CLAUDE.md exige revisión humana.
