# Progreso

> Preferencias del usuario: `docs/USER_PREFERENCES.md` (adelantar siempre lo que no dependa del usuario; respuestas breves).

**Fase actual:** F1 terminada. Decisiones G0 tomadas (ADR-014). Precio: Darwinex. **G0 no se cierra hasta auditar los datos de Darwinex, y para eso hacen falta las credenciales del usuario.** Piloto GDELT GKG 2024–2025 terminado.

## Hecho
- F0 cerrada: esqueleto, núcleo (`timeutils`, `config`, `runlog`, CLI), protocolo aprobado (ADR-009). Revisión adversarial F0 resuelta (ADR-008).
- Especificaciones de CL verificadas con el Rulebook NYMEX cap. 200 (ADR-005).
- F1:
  - `ingestion/rawstore.py`: almacén raw inmutable con manifiesto sha256 e idempotencia (16 tests).
  - Auditorías con evidencia en `data/raw/` y `manifests/`: GDELT, Databento (solo metadatos y presupuestos, sin compra), EIA, FRED/ALFRED, CFTC y NOAA. Informes en `reports/f1/`.
  - Informe consolidado G0: `reports/F1_source_audit.md`.
  - Monitor en vivo de publicación de GDELT (`data/raw/gdelt_lag_monitor/`). Se cortó con un reinicio del contenedor (~05:05 UTC) y se relanzó a las 07:25 UTC.
- D5 (aprobado 2026-10-15): ingesta GDELT GKG por streaming (`ingestion/gdelt_gkg.py`, `wna ingest gdelt-gkg`, ADR-013).
  - Plan: `docs/plans/FASE_02_gdelt_gkg.md`.
  - Revisión adversarial: 14 hallazgos. Corregidos los 1–7 y 9–14; el 8 es un riesgo aceptado (bajo).
  - Prueba real de un día: 2024-06-10, filtro v1.
  - Piloto 2024-01-01 → 2026-01-01 con filtro v2 terminado (`reports/f2/gdelt_gkg_pilot.md`):
    - 731 días, 0 fallidos, 0 ficheros 404;
    - 92,5 M filas leídas, 2,87 M conservadas;
    - 9,0 GB en disco.
    - Re-ejecución: 731 días verificados sin red.
  - Evidencia D6: 2 de 68.493 ficheros se publicaron más de 15 min después de su nombre (máximo +35,7 min).
- D12: captura prospectiva (`ingestion/capture.py`, `wna capture run`, `.github/workflows/capture.yml`, ADR-016).
  - Revisión adversarial: 11 hallazgos, todos corregidos.
  - Dos ejecuciones manuales en GitHub OK (crear la rama y actualizarla).
  - El cron programado empezó a correr solo a las 04:56 UTC, después de que el usuario hiciera un commit del workflow. Registró correctamente el salto 01:30→05:00 como `gap`.
- `make check` en verde (246 tests, cobertura de ramas 99 %).

## Pendiente
- Credenciales de Darwinex guardadas en la sesión (`~/.config/wna/secrets.env`, fuera del repo). **Bloqueo:** el FTP de Darwinex (tickdata.darwinex.com:21) no es accesible desde el entorno: el DNS resuelve, pero la conexión TCP agota el tiempo; el entorno solo deja salir HTTPS por el proxy. El acceso a la red del entorno ya es "Completo". Por el túnel del proxy (CONNECT) se abre la conexión, pero el servidor no completa el saludo FTP y la corta (3 intentos, 2026-10-16). Opción viable: el usuario descarga los ficheros en su ordenador y los sube.
- Auditoría Darwinex (F1 bis): cobertura, zona horaria, huecos, rollover, licencia y contraste con la EIA (futuro CL diario hasta 2024-04-05, ADR-015) → cierre de G0.
- Avisado al usuario: riesgos de D6 (+15 min: 2 ficheros tardíos) y D7 (EIA revisada).
- Revisión adversarial G0 hecha: 11 hallazgos resueltos o convertidos en decisiones (`reports/F1_source_audit.md` §6).
- Credenciales: guardadas solo en la sesión (`~/.config/wna/secrets.env`, fuera del repo). Recomendado regenerarlas (se compartieron en el chat) y ponerlas en la configuración del entorno.

## Bloqueos
- Sin credenciales de Darwinex no hay precio intradía. F2 de precio bloqueada.
- cmegroup.com, BLS y DOL inaccesibles (403) desde el entorno; GDELT DOC API con rate limit.
- La sesión llegó al límite de uso a las ~05:10 UTC; se reanudó a las 07:25 UTC.
