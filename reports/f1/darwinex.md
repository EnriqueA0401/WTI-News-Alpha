# F1 bis — Auditoría Darwinex XTIUSD M1 (SQX)

Fecha: 2026-10-16.
- **Fuente:** fichero compartido por el usuario desde Google Drive.
  - Archivo: `data/raw/darwinex/xti_m1_sqx_20261016_v2.bin` (RAR, 18,9 MB, manifiesto `manifests/darwinex.jsonl`).
  - Contiene `XTIUSD_darwinex_M1 - copia.csv`, de 105,7 MB, extraído en `data/raw/darwinex/extracted_v2/` con sha256 `ab0ede34…68aa`.
- **Scripts:** `scripts/f1_audit/darwinex_audit.py` y `darwinex_crosscheck.py`.
- **Licencia:** datos del bróker para uso propio. No se redistribuyen (el repo es público): solo se commitean manifiestos y estadísticas.

## 1. Formato y validez
- Columnas: `Date, Time, Open, High, Low, Close, TickVolume, AvgSpread`. Según el usuario, el OHLC es sobre el bid.
- **1.961.470 barras** de 2017-10-02 01:00 a 2026-07-21 02:59 (hora servidor).
- **Validez:**
  - 0 duplicados;
  - 0 precios ≤ 0;
  - 0 barras con High < max(Open, Close) o Low > min(Open, Close);
  - 0 spreads negativos;
  - 0 barras con TickVolume ≤ 0.

## 2. Cobertura
| Año | Barras |
|---|---|
| 2017 (desde oct) | 70.202 |
| 2018 | 327.210 |
| 2019 | 330.581 |
| 2020 | 343.988 |
| 2021–2023 | **0** |
| 2024 | 348.868 |
| 2025 | 347.367 |
| 2026 (hasta 21-jul) | 193.254 |

- **Hueco 2020-12-31 23:59 → 2024-01-05 02:00 (≈ 3 años).** Confirma lo que dijo el usuario. El resto de huecos de más de 3 días son festivos: Navidad, Año Nuevo, Semana Santa y 17–22 dic 2025.

## 3. Zona horaria (sin suponerla: inferida de los datos)
- La pausa diaria del CL (17:00–18:00 ET) aparece **siempre a las 00:00–00:59 hora servidor**, en todos los meses.
- Semana a semana, de martes a viernes, en 300 de 304 semanas no hay ninguna barra a las 00h servidor. Las otras 4 (may–jul 2018) tienen una sola barra suelta.
- El patrón no se mueve en los cambios de horario de EE. UU.
- Encaja también con la apertura del domingo (lunes 01:00 servidor = domingo 18:00 ET) y el cierre del viernes (23:59 servidor = 16:59 ET).
- **Conclusión:** hora servidor = **America/New_York + 7 h**, todo el periodo. Se convertirá a UTC con esa regla y con `timeutils` (horas de pared de NY).
- **Riesgo residual:** 2 semanas sin barras a las 23h o 01h (festivos) y las 4 barras sueltas de 2018.

## 4. Contraste con el futuro CL (EIA RCLC1/RCLC2, liquidación diaria, 2017-10 → 2024-04-05)
- **Método:** precio medio (bid + AvgSpread/2) del minuto 14:29 ET frente a la liquidación oficial (14:30 ET). 874 días.

| Medida | Valor |
|---|---|
| Mediana de la diferencia absoluta con el contrato 1 (C1) | 0,11 USD |
| Mediana con el contrato 2 (C2) | 0,06 USD |
| Días en que Darwinex está más cerca de C1 que de C2 | 36 % |
| p95 de la diferencia con C1 | 1,01 USD |
| Correlación de retornos diarios con C1 / C2 | 0,979 / 0,859 (n = 864, sin 20–21 abr 2020) |
| Spread medio a esa hora (mediana) | 0,044 USD |

- **Interpretación:**
  - El CFD sigue el futuro de cerca, con diferencias de céntimos, pero no siempre el mismo contrato: muchos días se parece más al segundo vencimiento.
  - Lo esperable es que el bróker role antes que el vencimiento del CL. Las peores diferencias (1,4–2,1 USD) se concentran en mar–abr 2020, con el contango extremo y el precio negativo de C1.
- **Consecuencia:** el CFD tiene **saltos en sus fechas de roll**. Antes de usarlo para etiquetas o retornos hay que detectarlas (cambios de base frente a C1/C2) y tratarlas como cruces de contrato: no hay retornos ni etiquetas a través del roll (CLAUDE.md §7).

## 5. Pendiente
1. **Re-descargar 2021-01 → 2024-01** en SQX (usuario).
2. Detectar las fechas de roll del CFD y documentarlas (F2).
3. Desde 2024-04-06 no hay referencia externa gratuita (ADR-015): limitación declarada.
