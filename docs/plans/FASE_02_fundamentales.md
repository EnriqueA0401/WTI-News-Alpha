# F2 — Fundamentales point-in-time (EIA WPSR, CFTC COT, FRED/ALFRED)

**Estado:** G0 aprobado (ADR-019). Reglas de hora ya decididas por el usuario: D7, D8, D9 y D10 (ADR-014). Este plan las aplica; no fija reglas nuevas. Si aparece un caso que las reglas no cubren → parada y pregunta.

Se hace en tres partes, en este orden: **A. WPSR**, **B. COT**, **C. FRED/ALFRED**. Cada parte tiene sus tests, su revisión adversarial y su commit.

## A. EIA WPSR (calendario de publicaciones + valores semanales)

### Entradas (raw de F1, con manifiesto en `manifests/eia.jsonl`; no hay descargas nuevas)
- `eia/web/wpsr_archive_index_2026-10-15.html`: fechas de publicación (una carpeta `archive/YYYY/YYYY_MM_DD/` por publicación).
- `fred/api/release_212/dates_2015.json`: 2.ª fuente de fechas (FRED release 212).
- `eia/web/wpsr_schedule_2026-10-15.html`: horas oficiales de las 14 excepciones de dic-2024 a nov-2026.
- `eia/api/seriesid_*.json`: 11 series semanales (valores actuales, revisados: D7).
- `eia/web/archive/wpsr_YYYY_MM_DD.php.html` (42 páginas): "week ending" de cada página, que se usa **solo para validar**.

### Reglas
1. **Fecha de publicación verificada:** está en el índice del archivo **y** en FRED 212.
2. **Semana → primera publicación:** la primera publicación con fecha ≥ viernes de cierre + 5 días.
   - Varias semanas pueden caer en la misma publicación: es el caso de las 2 semanas sin publicación propia (2022-06-17 y 2023-11-03). Cota conservadora: aparecen como "semana previa" en la siguiente.
   - **Validaciones duras:**
     - toda publicación recibe ≥ 1 semana;
     - retraso ≤ 12 días;
     - solo 2 semanas por publicación en las fechas conocidas;
     - coincide con las 14 excepciones oficiales y con las páginas "week ending" del archivo (salvo 2019-07-03, cuyo CSV está mal asociado: F1).
3. **Hora (`available_at`):**
   - **(a)** Fecha en la tabla oficial de excepciones → su hora oficial (la primera, la del CSV). Estado `official_exception`.
   - **(b)** Si no, miércoles con retraso de 5 días (calendario normal) → **10:30 ET** (D8, riesgo declarado). Estado `d8_standard`.
     - Evidencia: en dic-2024 → nov-2026 todas las excepciones oficiales son no-miércoles.
   - **(c)** Resto de fechas verificadas (publicaciones desplazadas) → **18:00 ET** de ese día (D9 / R-PIT 1). Estado `rpit_date_verified`.
   - **(d)** Fecha solo en el archivo, no en FRED 212 (2020-01-03) → **18:00 ET del siguiente día laborable** (R-PIT 2). Estado `rpit_date_unverified`.
4. **Valores:** los de la API (revisados) → `revision_risk = true` en todas las filas (D7).
   - WCESTUS1 con semana < 2016-10-07 → además `known_redefinition = true` (−31 a −34 Mb frente a lo publicado, F1 §4).
5. Las semanas posteriores a la última publicación del índice se excluyen y se cuentan.

### Salidas
- `data/normalized/eia_wpsr/<version>/releases.parquet`: `release_date`, `available_at` (UTC), `time_status`, `n_weeks`.
- `data/normalized/eia_wpsr/<version>/values.parquet`: `series_id`, `week_ending`, `value`, `units`, `release_date`, `available_at`, `time_status`, `revision_risk`, `known_redefinition`.
- Manifiesto con sha256 de entradas y salidas y el conteo por `time_status`.

### Tests (antes de implementar)
- Asignación semana → publicación:
  - caso normal;
  - jueves por festivo;
  - lunes 2025-12-29 (semana 12-19) seguido del miércoles 12-31 (semana 12-26);
  - semana sin publicación propia;
  - publicación sin semana → error;
  - retraso > 12 → error.
- Hora:
  - excepción oficial (11:00 y 12:00, incluido el texto "a.m./p.m.");
  - miércoles normal 10:30 EST/EDT a UTC;
  - jueves desplazado 18:00 ET;
  - fecha no confirmada → siguiente laborable (viernes → lunes).
- Parseo:
  - índice, FRED 212 y tabla de excepciones (HTML sintético);
  - JSON de la API (periodo no viernes → error; duplicado → error; valor no numérico → error).
- Validación contra las páginas "week ending".
- Escritura inmutable más manifiesto y hash determinista (helper compartido con el precio).

## B. CFTC COT (resumen; se detallará al llegar)
- **Fuente:** Disaggregated Futures Only 067651 (zips anuales más API).
- **`available_at`:**
  - `created_at` real desde 2022-10;
  - antes, fecha oficial más R-PIT (18:00 ET; fecha dudosa → siguiente laborable).
  - El margen sobre `created_at` **no está fijado** → se preguntará.

## C. FRED/ALFRED (resumen)
- **Series:** DGS10, DGS2, T10YIE, VIXCLS y DTWEXBGS (desde 2019-02-04, D10).
- **Regla:** `available_at` = 00:00 ET del día siguiente a la vintage (D9 / R-PIT 4).
- Las macro mensuales (PAYEMS…) quedan fuera de las features intradía mientras su hora no esté verificada.

## Riesgos
- La hora de 10:30 en miércoles 2015–2024 no está verificada (D8, aceptado por el usuario).
- Valores revisados (D7).
- La regla "≥ +5 días" depende del patrón observado; las validaciones duras la protegen.
