# Conocimiento del warehouse

El proyecto y los datasets habilitados no van acá: los define el `.env`
(`WAREHOUSE_PROJECT`, `WAREHOUSE_DATASETS`) y llegan a las instrucciones de la
skill como variables. Esta página es para notas técnicas de BigQuery.

## Notas técnicas de BigQuery

- Las vistas regionales (`region-us.INFORMATION_SCHEMA...`) no están habilitadas;
  usá `INFORMATION_SCHEMA` del dataset o `<dataset>.__TABLES__` (tamaño y filas).
- Fechas: `DATE_TRUNC(fecha, MONTH)`, `DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)`.
- Para tasas, calculá numerador y denominador por separado y dividí con `SAFE_DIVIDE`.
- Para pronósticos o anomalías existen `AI.FORECAST` y `AI.DETECT_ANOMALIES` dentro
  de BigQuery: preferílos a traer datos crudos.

## Glosario, métricas y tablas

No van acá: viven en la base de conocimiento (`data/knowledge.db`), con autor y
estado. Leelos con `knowledge_get` y `knowledge_search`; sumalos con
`knowledge_propose` o desde el panel Conocimiento.
