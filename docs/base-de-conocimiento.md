# Base de conocimiento

El agente responde mejor cuando sabe lo que el equipo sabe de sus datos: qué cuenta
como venta, qué filtros van siempre, qué día los datos están mal, cómo se cruzan dos
tablas. La base de conocimiento guarda eso con autor, aprobación y vencimiento, y el
agente la consulta antes de elegir una tabla o escribir una consulta.

## Tres capas

| Capa | De dónde sale | Autor | Quién la ve |
| --- | --- | --- | --- |
| **Catálogo** | Metadatos de BigQuery: descripción de tablas y columnas, tipos, partición, filas, última actualización | `sistema` | Todos |
| **Conocimiento compartido** | Lo propone cualquiera (o el agente en su nombre) y lo aprueba un curador | El autor y quien aprobó | Todos |
| **Notas privadas** | Lo que una persona anota para sí | El autor | Solo el autor |

El catálogo se sincroniza solo: al arrancar el servidor, si tiene más de
`CATALOG_SYNC_HOURS` horas, y a pedido de un curador. Nadie lo edita, y el
conocimiento de las personas nunca lo pisa.

## Qué se guarda

Cada entrada es sobre algo concreto: una tabla, una columna, una métrica, un término o
un join. Tipos de entrada:

| Tipo | Ejemplo |
| --- | --- |
| Descripción | "Una fila por pedido; los cancelados quedan con estado `Cancelado`." |
| Definición de métrica | "Ventas netas" con su SQL |
| Regla de filtro | "Excluir siempre las sesiones con `es_bot`." |
| Advertencia | "El 12 de septiembre los pedidos están duplicados." |
| Join | Cómo se cruzan `pedidos` e `items_pedido` |
| Sinónimo | "Facturación" = ventas brutas |
| Consulta de ejemplo | Un SQL que responde una pregunta frecuente |

Además, cada entrada puede llevar:

- **Fuentes:** links, documentos o la conversación de la que salió, para verificarla.
- **Vencimiento:** una fecha a partir de la cual se considera vencida. Sirve para
  advertencias sobre un período puntual.

## Nada se edita en el lugar

Una corrección es una entrada nueva que reemplaza a la anterior. La original sigue
vigente hasta que se aprueba la corrección (al instante, si es una nota privada) y
después queda como deprecada. Así se conserva la historia completa, con sus autores.

| Estado | Qué significa |
| --- | --- |
| Propuesta | Esperando aprobación. El agente puede usarla, pero avisa que no está aprobada. |
| Aprobada | Vigente. |
| Rechazada | No se usa. |
| Deprecada | Reemplazada o dada de baja. No se usa. |

Aprueban y deprecan lo compartido los curadores (`KNOWLEDGE_CURATORS`) y los admins.
Una nota privada la depreca su autor.

## Cómo la usa el agente

- **Índice corto en las instrucciones.** El agente recibe qué tablas y métricas están
  documentadas, una línea por cada una (hasta `KB_INDEX_MAX_LINES`). El detalle no va
  en el prompt.
- **Herramientas de lectura.** `knowledge_get` trae lo que hay sobre una tabla o
  métrica; `knowledge_search` busca por texto (FTS5 de SQLite). Cada entrada vuelve con
  su procedencia: `[compartido · autor ana · aprobado por bruno]`, `[nota privada
  tuya]`, `[propuesta, sin aprobar]`.
- **Prioridad.** Compartido aprobado, después notas privadas de la persona, después
  catálogo, después propuestas sin aprobar. Si dos entradas se contradicen, el agente
  lo dice y cita a los autores.
- **Vencidas y fuentes.** Una entrada vencida vuelve marcada y el agente no la aplica
  sin avisar. Si tiene fuentes, las cita.

## Cómo se suma conocimiento

1. **En el chat.** Cuando corregís o aclarás algo, el agente propone guardarlo y
   muestra cómo quedaría. Solo se guarda cuando lo confirmás en otro mensaje: la
   herramienta `knowledge_propose` exige que la confirmación llegue en otro turno, así
   que el agente no puede guardar nada por su cuenta.
2. **En el panel Conocimiento.** Por tabla o métrica: leer, proponer, corregir,
   aprobar, rechazar y deprecar.

Al proponer, el SQL pasa por las mismas guardas que las consultas (solo lectura,
datasets habilitados). Al aprobar, se valida además con un dry run. Los nombres cortos
(`pedidos`, `dataset.tabla`) se completan con el catálogo, y una columna que no existe
se rechaza.

## Exportación a OKF

Lo compartido y aprobado se exporta como un bundle
[OKF v0.2](https://okf.md/spec/): Markdown con frontmatter YAML, legible en git y
fácil de leer para otros agentes sin pasar por la API.

```
knowledge-okf/
├── index.md
├── log.md        ← altas y bajas por fecha
├── tables/       ← una por tabla: esquema, columnas, reglas, advertencias
├── metrics/
├── terms/
└── joins/
```

- La exportación es de una sola vía: el bundle no se edita a mano ni se reimporta.
- Se regenera sola al aprobar o deprecar algo compartido y después de cada
  sincronización del catálogo (`KNOWLEDGE_OKF_AUTO`). Un curador también puede
  exportarla desde el panel.
- Las notas privadas, las propuestas y lo deprecado no se exportan.
- El exportador solo reemplaza una carpeta vacía o una que generó él (la marca
  `.okf-export.json`).

## Implementación

| Pieza | Dónde |
| --- | --- |
| Base SQLite con FTS5, versionado por reemplazo, eventos | `knowledge/store.py` |
| Reglas: visibilidad, curadores, validación, prioridad, procedencia | `knowledge/service.py` |
| Sincronización del catálogo | `knowledge/catalog_sync.py` |
| Herramientas del agente | `knowledge/tools.py` |
| Exportación OKF y chequeo de conformidad | `knowledge/okf_export.py` |
| API (`/api/knowledge`) | `server/knowledge_api.py` |
| Panel Conocimiento | `web/src/components/app/knowledge-panel.tsx` |
