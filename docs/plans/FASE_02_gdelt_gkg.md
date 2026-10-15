# F2 (parcial) — Ingesta GDELT GKG por filtrado en streaming (D5 / ADR-011)

**Estado:** D5 aprobado por el usuario (2026-10-15). El resto de F2 sigue pendiente de G0.

**Objetivo:** extracto filtrado, reproducible y verificable del GKG 2.1 para el piloto 2024–2025, sin guardar los zip originales (≈ 400 GB de transferencia).

## Entradas
- `data/raw/gdelt/index/masterfilelist_20261015.txt` (verificado contra `manifests/gdelt.jsonl`): URL, tamaño y md5 de cada fichero.
- `config/gdelt.yaml`: filtro versionado y parámetros operativos.

## Salidas
- `data/raw/gdelt_gkg_oil/<filter_version>/YYYY/MM/YYYYMMDD.parquet`: un fichero por día UTC, solo lectura y escrito una vez.
  - Contiene las 27 columnas originales (texto, sin rellenos) de las filas que pasan el filtro.
  - Añade `source_file_ts` (UTC), `row_in_file`, `match_theme`, `match_title` y `match_org`.
- `manifests/gdelt_gkg_oil/<filter_version>/YYYY-MM.jsonl.gz`: una línea por día con el sha256 de la salida, el commit, la versión del filtro y el detalle de cada fichero de origen:
  - md5 y tamaño del índice, verificados;
  - `Last-Modified`;
  - estado HTTP;
  - filas leídas, conservadas y rechazadas.

## Filtro `gkg-oil-v1` (alta sensibilidad; la relevancia fina se decide en F3)
Una fila se conserva si cumple alguna de estas condiciones:
- **(a) Tema:** algún tema de `V1Themes` contiene un token de petróleo entre `_` (OIL, OILPRICE, PETROLEUM, CRUDE, OPEC, REFINERIES, PIPELINE…). Se excluyen los temas `TAX_FOODSTAPLES_*` (aceite de oliva o de coco). Por eso SOIL no cuenta.
- **(b) Título:** `PAGE_TITLE` contiene crude, oil, opec, wti, brent, petroleum, refinery, gasoline o pipeline.
- **(c) Organización:** `V1Organizations` contiene OPEC.

En la muestra de F1 conserva un 3–6 % de las filas.

## Invariantes críticos
1. **El raw es inmutable:**
   - cada salida se escribe una vez (enlace exclusivo) y queda en solo lectura;
   - una salida sin manifiesto, o con checksum distinto, lanza `RawIntegrityError`;
   - el código se niega a ejecutar con el árbol git sucio.
2. **Integridad de origen:**
   - el md5 y el tamaño de cada zip se comparan con el índice; si difieren, el día falla;
   - el zip debe tener exactamente un miembro.
3. **Sin fallos silenciosos:**
   - una línea con un número de campos distinto de 27 se rechaza y se cuenta, marcando si parecía relevante;
   - si los rechazos superan el umbral del día, el día falla;
   - un 404 se registra como `missing_404` (el índice no garantiza existencia, F1 §2);
   - un error de red se reintenta con backoff acotado y, si persiste, el día falla y se reporta;
   - el UTF-8 inválido se decodifica con reemplazo y se cuenta.
4. **Idempotencia:**
   - un día ya registrado, con checksum válido, no vuelve a tocar la red;
   - un día fallido se reintenta en la siguiente ejecución.
5. **Determinismo:** las mismas entradas producen el mismo sha256 de salida (orden explícito por `source_file_ts` y `row_in_file`).
6. **Disco:** antes de cada día se comprueba el espacio libre mínimo; por debajo, la ejecución para.

## Tests (antes de implementar)
- Índice:
  - líneas válidas, truncadas (contadas), md5 inválido y timestamps duplicados;
  - rango `[start, end)`;
  - agrupación por día.
- Filtro:
  - tokens de tema;
  - exclusión de oliva/coco y de SOIL;
  - título, organización;
  - línea partida rechazada y marcada;
  - salto final;
  - orden y tipos.
- Descarga:
  - md5 o tamaño distintos;
  - 404;
  - reintento tras 503;
  - fallo de red persistente;
  - registro de `Last-Modified`.
- Día:
  - salida de solo lectura más manifiesto;
  - segunda ejecución sin red;
  - salida huérfana o alterada;
  - umbral de rechazos;
  - hash determinista;
  - árbol sucio.
- Ejecución en paralelo con un ejecutor inyectado.

## Riesgos
- Tamaño: ≈ 5 GB/año en Parquet con GCAM (estimado); se mide en el piloto. El histórico completo podría no caber en disco con GCAM; se decidirá con cifras.
- La línea partida por un salto interno (F1) se rechaza en lugar de repararse. Su frecuencia (≈ 1 de cada 175.000 filas) se medirá.

## Hecho cuando
- `make check` en verde.
- Revisión adversarial resuelta.
- Piloto ejecutado con informe `reports/f2/gdelt_gkg_pilot.md`: días OK o fallidos, 404, rechazos, tamaño y filas por mes.
