# Plan F4 — Features point-in-time

**Estado:** G2 aprobada (ADR-034). Salida de la fase: tests de truncamiento y canarios en verde (CLAUDE.md §12). No hay puerta formal; el siguiente punto de parada es G3 (F5).

## Objetivo
Para cada `decision_time` de la rejilla, una fila con lo que se sabía en ese instante: mercado, fundamentales, posicionamiento, noticias/eventos y calendario. Sin ningún dato con `available_at > decision_time`.

## Entradas (todas con `available_at` explícito)
| Capa | Versión | Uso |
|---|---|---|
| Precio XTI 1 min (Darwinex, bid) | `dwx-xti-m1-v3` | mercado; define cuándo hay sesión |
| WPSR EIA | `eia-wpsr-v2` | último inventario/producción/importaciones/utilización publicados |
| COT CFTC | `cftc-cot-v2` | posición neta y cambio de *managed money* |
| FRED/ALFRED | `fred-alfred-v2` | vintage vigente en t (`available_at ≤ t < superseded_at`) |
| Eventos | `events-v3` | artículos elegibles de 5 familias, eventos y métricas acumuladas por artículo |
| Calendario de releases y sesiones | `config/*.yaml` | proximidad a WPSR/COT, hora ET, día, festivo |

## Rejilla de decisión (PROTOCOL A4, aprobado)
- `decision_time` cada 5 min, en UTC, solo si la última barra de 1 min completa (`available_at ≤ decision_time`) tiene ≤ 5 min de antigüedad. Así no se decide con el mercado cerrado ni en la pausa diaria.
- Solo en los periodos de precio aprobados en G0 (2018–2020 y 2024–2026, ADR-019).

## Salidas
- `data/features/<feature_version>/features_YYYYMM.parquet`, publicada con `normalization/store.py` (inmutable, manifiesto con sha256, parámetros y hash de código).
- Columnas fijas: `decision_time`, `data_cutoff_time` (= `decision_time`), `feature_version`, y un `*_available_at` por grupo (mercado, wpsr, cot, fred, noticias).
- `docs/DATA_DICTIONARY.md` actualizado con cada feature, sus unidades y su ventana.

## Features (menú inicial, Documento §7; se podarán en F7 solo con train)
1. **Mercado**, solo barras completas:
   - retornos log a 1, 5, 15 y 60 min y a 1 sesión;
   - rango y volatilidad realizada a 60 min y a 1 sesión;
   - volumen de ticks relativo a su hora: la media de las 20 sesiones previas, solo pasado;
   - spread medio;
   - minutos desde la última barra (marca los huecos).
   - Ningún retorno cruza un hueco mayor que el umbral configurado: en ese caso, nulo marcado (sin rellenar).
2. **Fundamentales WPSR:**
   - último valor publicado y cambio semanal de crudo comercial, Cushing, gasolina, destilados, producción, importaciones y utilización;
   - horas desde la publicación.
3. **COT:** neto de *managed money*, su cambio y horas desde la publicación.
4. **FRED:** valor de la vintage vigente de cada serie (5 diarias) y la sorpresa ingenua respecto a la anterior.
5. **Noticias/eventos (`events-v3`):**
   - artículos únicos elegibles en 15 min, 1 h y 4 h, en total y por familia;
   - eventos nuevos (`is_event_first`);
   - fuentes distintas;
   - edad del último evento por familia.
   - **Época** (`title_source` = slug o título, ADR-033/034) como indicador.
   - **Sin mecanismo** (ADR-032).
6. **Calendario:**
   - hora y día en ET;
   - minutos hasta el próximo WPSR y COT programados, según el calendario que se conocía antes de esa fecha;
   - festivo y vísperas;
   - minutos desde la apertura de la sesión.

## Invariantes críticos (CLAUDE.md §7)
- En cada fila, `max(*_available_at) ≤ decision_time`. Si no se cumple, `LeakageError`.
- Todos los `asof` son backward sobre `available_at`. Una coincidencia exacta (`available_at == decision_time`) **se incluye**, porque el dato ya era visible. Tiene un test.
- Nada se ajusta con datos globales: ni medias, ni z-scores, ni cuantiles. Las normalizaciones van en F7, dentro de cada fold. El volumen relativo usa solo sesiones previas.
- Sin `fillna`/`ffill` sueltos. Los "último valor conocido" son `asof` con su `available_at` y la antigüedad como feature.

## Tests (primero, en los críticos)
- Casos a mano:
  - rejilla con DST, pausa diaria y festivo;
  - retornos que cruzan un hueco → nulo;
  - WPSR desplazado por festivo: invisible 1 s antes de su `available_at` y visible en ese instante;
  - vintage de FRED reemplazada.
- **Truncamiento** (`hypothesis`): las features en t son idénticas con el dataset completo y con el truncado en t, en todas las fuentes.
- **Canario:** inyectar un artículo o un dato con `available_at` futuro → `LeakageError`.
- Determinismo: el mismo hash de salida al re-ejecutar.

## Criterio de hecho
- `make check` en verde con ≥ 95 % de cobertura de ramas.
- Ejecución sobre un mes real antes del periodo completo.
- Revisión adversarial por subagente resuelta.
- Manifiesto publicado.
- `PROGRESS.md` y `DATA_DICTIONARY.md` al día.

## Riesgos
- XTI de Darwinex es un CFD (bid), no el futuro CL. Ya aceptado en G0 (ADR-019).
- El cambio de época de las noticias (2018-2019 frente a 2019-09 en adelante) puede confundirse con señal: indicador de época, y evaluación por época en F8.
- Volumen: unos 70.000 instantes de decisión por año, decenas de columnas. Cabe en disco.
