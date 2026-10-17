# Plan F5 — EDA y estudio de eventos (Documento §7.1–7.2)

**Puerta de salida: G3**. Hay suficiente diversidad de eventos para comparar, la incertidumbre es explícita y no se ven errores de datos.
**Datos:** solo el periodo de investigación, 2018–2020 y 2024-01 → 2025-06 (ADR-036). Nada del periodo sellado: ningún `t0` ni ventana posterior al evento puede entrar en él.

## Objetivo
Describir, sin modelos, qué pasa con el precio alrededor de los eventos de las 5 familias:
- tamaño, dirección, duración e incertidumbre;
- frente a placebos y momentos comparables sin evento.

Es descriptivo: no fija umbrales ni elige features para F7.

## Entradas
- `events-v3`: `events.parquet` y artículos. t0 = `event_first_available_at` (primera disponibilidad accionable).
- `dwx-xti-m1-v3`: barras bid de 1 min.
- `feat-v1`: régimen de volatilidad, hora y época, solo como segmentación.

## Medidas por evento
1. **Precio de referencia:** cierre bid de la primera barra completa disponible tras `t0 + latencia`. *(La latencia es una decisión pendiente.)*
2. **Retornos log a 5, 15, 30 y 60 min,** en tiempo de mercado, y **a fin de sesión, 1 y 3 sesiones.**
   - *(La referencia de fin de sesión es una decisión pendiente.)*
   - Si la ventana cruza la pausa diaria, el fin de semana o un hueco de más de 5 min, la medida queda nula (sin rellenar).
3. **MFE/MAE a 60 min:** máximo movimiento favorable y adverso desde la entrada, con high/low bid. "Favorable" se mide en las dos direcciones, porque el mecanismo no es fiable (ADR-032).
4. **Volatilidad posterior:** RV de 60 min tras t0 frente a la RV de los 60 min previos.
5. **Tiempo hasta |retorno| ≥ k × ATR previa y tiempo hasta la retracción a la mitad.** k = 1, fijo y descriptivo.

## Agrupación e incertidumbre
- **Un evento = una observación.** Los artículos del mismo evento no se cuentan aparte.
- Medianas y medias recortadas, con bandas por **bootstrap por bloques**: bloques de un día, para que los eventos del mismo día no cuenten como independientes. Semilla fija.
- Se reporta siempre el **n de eventos independientes**.
- **Segmentos:**
  - familia;
  - novedad (primer evento de su familia en 24 h o no);
  - nº de fuentes en la primera hora, solo con información disponible en ese momento;
  - régimen de volatilidad (terciles de la RV previa, ajustados solo con 2018–2020 y aplicados al resto);
  - hora de sesión (Asia/Europa/EE. UU.);
  - época (slug o título);
  - año.

## Controles
- **Placebo:** el mismo número de t0 falsos, tomados de momentos comparables (mismo día de la semana y hora ±30 min, mismo año) sin evento de esa familia en ±4 h. 1.000 permutaciones con semilla fija.
- **Línea base sin noticias:** distribución de |retorno| en todos los instantes de la rejilla sin evento en ±4 h.
- **Relevancia baja:** artículos indirectos de las mismas fechas como pseudo-eventos.
- **Comprobación de datos:**
  - el evento más extremo de cada familia, con su fuente rastreable (`article_id` y URL);
  - eventos con precio ausente.

## Salidas
- `src/wti_news_alpha/eda/event_study.py`: lógica pura y testeada. Retornos sin cruzar huecos, entrada tras la latencia, MFE/MAE con lo adverso primero en empates y bootstrap por bloques.
- `scripts/f5/*.py`: orquestación.
- `reports/G3_eventos.md`: tablas y figuras (PNG, sin titulares).

## Tests
- Casos a mano: entrada, retornos, hueco, pausa diaria, fin de sesión, MFE/MAE con empate en la misma barra y bootstrap con semilla.
- Propiedad: el resultado para el evento e no cambia si se truncan los datos posteriores a su ventana.
- Guardia: si un `t0` o una ventana cae en el periodo sellado, error explícito.

## Riesgos
- CFD bid, no el futuro CL (ADR-019).
- Muchos eventos de un mismo día, que se tratan con bloques de un día.
- La época 2018–2019 (slug) frente a la de título.
- Familias con pocos eventos (`weather`) darán intervalos anchos.
