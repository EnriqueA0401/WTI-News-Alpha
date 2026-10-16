# Guía de etiquetado — muestra auditada G2 (v1)

**Para qué sirve:** medir cuánto aciertan las reglas automáticas (`rules-v1`) al decir si una noticia es relevante para el petróleo WTI, de qué tipo es y en qué dirección empuja. Tu etiqueta es la "verdad". La propuesta que verás es de Claude (ADR-028): confírmala si es correcta y corrígela si no.

Solo se ve el **titular**, el medio, la fecha y el enlace, porque no hay texto completo. Si con eso no basta, usa "no sé" / "ambiguo". Puedes abrir el enlace, pero no hace falta.

## 1. Relevancia
| Etiqueta | Cuándo |
|---|---|
| **Directa** | Trata del petróleo crudo o de algo que mueve su oferta o demanda: precio del crudo, OPEP+, producción, exportaciones, inventarios, refinerías, oleoductos, sanciones a petroleras o a países productores, ataques a instalaciones o petroleros. |
| **Indirecta** | Contexto que puede influir pero no es del petróleo en sí: economía de EE. UU. o China, tipos de interés, dólar, guerra en una zona productora sin mencionar el petróleo, gas natural, electricidad. |
| **No relevante** | Nada que ver: "crude bomb", aceite de cocina, política local, deportes, empresas de otros sectores. |

**Casos difíciles:**
- Una opinión sobre el petróleo es **directa** y su tipo es **opinión**.
- Una noticia de precios de gasolina en un país (subsidios locales) es **indirecta**, salvo que hable del crudo.

## 2. Tipo de evento (solo si es directa o indirecta)
| Tipo | Ejemplos |
|---|---|
| Inventarios EIA | "US crude stocks fall", informe semanal EIA/API, Cushing |
| OPEP+ | Cuotas, recortes, reuniones, cumplimiento |
| Interrupción de producción | Paradas, averías, fuerza mayor, incendios en refinerías |
| Sanciones / geopolítica | Sanciones, embargos, tope de precio, negociaciones, tensiones |
| Conflicto / ataque | Ataques, drones, misiles, guerra con efecto sobre el petróleo |
| Clima | Huracanes, tormentas, heladas |
| Logística | Petroleros, rutas marítimas (Ormuz, mar Rojo), puertos, oleoductos |
| Demanda / macro | Demanda, PIB, China, recesión, viajes |
| Tipos / dólar | Fed, inflación, dólar |
| Empresa | Resultados o fusiones de petroleras |
| Informe de mercado | "Oil prices rise / fall …" sin causa concreta |
| Opinión | Columnas y análisis |
| Otro | Ninguno de los anteriores |

## 3. Mecanismo económico (dirección)
| Mecanismo | Cuándo |
|---|---|
| Estrecha oferta | Menos crudo disponible: recortes, ataques, sanciones nuevas, caída de inventarios |
| Amplía oferta | Más crudo: subidas de producción, fin de sanciones, inventarios que suben |
| Aumenta demanda | Más consumo esperado |
| Reduce demanda | Menos consumo esperado |
| Eleva riesgo | Amenazas, tensiones, incertidumbre, sin cambio físico todavía |
| Ambiguo | No queda claro o no aplica (también en informes de mercado) |

**No fuerces una dirección.** Si el titular no lo dice, marca "ambiguo".

## 4. Calidad
- Se guardan por separado la propuesta de Claude y tu etiqueta, y se mide el porcentaje que corriges.
- Una fracción (≈ 10 %) aparece repetida a ciegas para medir tu propia consistencia (§16.3 del Documento).
