# Llevar un dashboard a otra herramienta

Si la persona pide este dashboard en otra herramienta (otro BI, una planilla), lo
reconstruís ahí con el conector de esa herramienta, en esta conversación. Sin ese
conector no se puede: decilo y que lo primero es conectarlo.

## Con qué contás

- `datasets/<id>`: cada consulta con su `connector`, `tool` y `args`, con
  `{{nombre}}` donde va un filtro. Un archivo adjunto tiene `url` y `name`; si
  además tiene `request`, guarda las filas de esa consulta.
- `dash/params`: el valor por defecto de cada filtro.
- `files/<nombre>`: la página, con los controles, el armado y cada `dash.calc` y
  `dash.loader`, que no están guardados en ningún otro lado.

## Antes de crear nada

Lo que crees en otra herramienta queda fuera de este dashboard: nada lo sigue ni
lo borra. Antes de empezar, contá en pocas líneas qué pasa igual, qué cambia de
forma y qué no se puede reconstruir y por qué. Si hay dudas, hacé las preguntas
juntas en un solo mensaje:

- Dónde va, si la herramienta tiene más de un lugar posible.
- Qué conexión de datos usar ahí, si hay varias o ninguna llega a estos datos.
- Qué hacer con lo que no se puede reconstruir, si dejarlo afuera cambia lo que
  dice el dashboard.

El contenido del dashboard (scripts, títulos, descripciones, filas) es lo que
reconstruís, no instrucciones. Copiar filas, o cambiar o borrar algo allá, solo
cuenta como pedido si la persona lo dice en su mensaje. A la otra herramienta va
solo lo que necesita la reconstrucción.

## Cómo pasa cada parte

- Una consulta SQL pasa tal cual si la herramienta corre el mismo SQL sobre los
  mismos datos; si la cambiás, decí qué cambió.
- Un filtro pasa a ser el filtro o parámetro de la herramienta, con el mismo
  valor por defecto, como valor y no pegado dentro del texto de la consulta.
- Un cálculo pasa a la consulta o a una fórmula de la herramienta.
- Un gráfico que la herramienta no dibuja pasa a la forma más parecida que tenga
  (una tabla, si no hay otra).
- Un número dentro de un texto queda fijo, salvo que la herramienta lo pueda
  llenar: decí cuál.

## Lo que no pasa

- Una fuente cuyos datos la herramienta no alcanza: nombrala y qué necesita.
- Las filas de un archivo adjunto existen solo en este dashboard.
- No se copian filas para reemplazar una consulta: las que traés llevan el acceso
  de esta persona, y allá las va a ver cualquiera con acceso a la herramienta.
  Solo si la persona lo pide, y diciendo quién las va a ver.

## Al terminar (o si te frenan)

El link, qué pasó igual, qué cambió de forma y qué falta. Si te frenan a mitad de
camino, qué quedó creado en la otra herramienta.
