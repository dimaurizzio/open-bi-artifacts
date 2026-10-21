---
name: bigquery-warehouse
description: Consultar el warehouse de BigQuery por MCP para responder preguntas sobre datos. Usar ante cualquier pregunta que se responda con una consulta ("cuántos", "traeme", "cómo evolucionó", "qué tablas hay", "fijate en la tabla X"), antes de armar un análisis o un dashboard, y para explorar esquemas.
metadata:
  adk_inject_state: true
---

# Consultas al warehouse

Warehouse habilitado:

- Proyecto de ejecución y facturación: `{wh_project}`
- Datasets habilitados: {wh_datasets}

Tenés las herramientas del servidor MCP de BigQuery: `list_dataset_ids`,
`list_table_ids`, `get_dataset_info`, `get_table_info`, `execute_sql_readonly` y
`get_query_results`. La plataforma fuerza estas reglas en cada llamada (si no se
cumplen, la herramienta devuelve `error`):

- Solo una sentencia `SELECT` o `WITH`. Nada que escriba.
- Solo los datasets habilitados.
- Hasta {wh_max_gib} GiB escaneados por consulta (se mide con un dry run previo).

## Cómo trabajar

1. **Ubicá la tabla.** Mirá el índice de conocimiento de abajo y usá
   `knowledge_search` o `knowledge_get` (definiciones, filtros obligatorios,
   advertencias, esquema del catálogo). Si no alcanza, `list_table_ids`. No adivines
   nombres. Las notas técnicas de BigQuery están en `references/warehouse.md`.
2. **Mirá el esquema antes de consultar** con `get_table_info`: columnas, tipos,
   descripción, partición y clustering. Si la tabla está particionada, filtrá
   siempre por la columna de partición.
3. **Agregá en SQL.** Traé resultados agregados (GROUP BY, COUNT, SUM), no filas
   de detalle. Ves como máximo {wh_max_rows} filas por consulta.
4. **Nombres completos** entre backticks, por ejemplo `` `{wh_example_table}` ``.
   `INFORMATION_SCHEMA` va por dataset: `` `{wh_example_dataset}.INFORMATION_SCHEMA.COLUMNS` ``.
5. **Si la consulta falla**, leé el error, corregí y reintentá una vez. Si vuelve
   a fallar, contale al usuario qué pasó.

## Conocimiento guardado

Índice de lo que está documentado (solo qué hay, no el contenido; para leerlo usá
`knowledge_get`):

{kb_index}

Cada entrada llega con su procedencia, por ejemplo
`[compartido · autor ana · aprobado por bruno · 2026-10-14]`, `[nota privada tuya]`
o `[propuesto por ana, sin aprobar]`.

- **Prioridad:** compartido aprobado > notas privadas del usuario > catálogo de
  BigQuery > propuestas sin aprobar.
- **Contradicciones:** si dos entradas dicen cosas distintas, decilo y citá a los
  autores. No elijas en silencio.
- **Propuestas sin aprobar:** usalas si no hay nada mejor, aclarando que no están aprobadas.
- **Vencidas** (`vencida desde …`): no las apliques sin avisar al usuario.
- **Fuentes:** si una entrada tiene fuentes, citalas al usarla.
- **Para sumar conocimiento** usá `knowledge_propose` en dos pasos: borrador, y
  guardado solo con la confirmación del usuario en su próximo mensaje.
- **Dónde va cada cosa:** lo que es de una tabla o de una columna (qué es, filtros,
  advertencias) va sobre esa tabla o columna, con el nombre de la tabla habilitada.
  Si te pasan la ficha de la tabla de origen (otro proyecto o dataset que lee una
  vista habilitada), guardala sobre la habilitada y mencioná el origen. Métricas,
  términos y joins son para lo que no es de una sola tabla.
- **Un pedido con muchas entradas:** armá todos los borradores en el mismo turno y
  pedí una sola confirmación. No armes borradores nuevos en el turno en que guardás:
  esos necesitan otra confirmación.

## Cómo responder

- Mostrá la consulta SQL que corriste en un bloque ```sql.
- Mostrá el resultado como tabla Markdown (hasta ~20 filas; si hay más, decilo).
- Después del resultado, una o dos oraciones con lo que muestran los datos.
  No inventes causas que los datos no muestran.
- No leas datos personales (emails, nombres, documentos) si no te los pidieron.
  Preferí métricas agregadas.

## Cuándo pasar a un artifact

Si el usuario pide un **dashboard**, un tablero, algo que se actualice solo, un
**análisis** o un informe para compartir, cargá la skill `dashboard`. Usa las
mismas consultas que acabás de probar: guardá la llamada que funcionó tal cual.
