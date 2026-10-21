# Guía de uso

Qué podés hacer con Open BI Artifacts y cómo. Pensada para quien usa la app, no para
quien la instala.

## El chat

Preguntale en lenguaje natural. El agente busca qué sabe el equipo sobre esos datos,
elige la tabla, escribe el SQL y responde con el número, la consulta que corrió y de
dónde sale.

- **Preguntas puntuales:** "¿Cuánto vendimos ayer por canal?"
- **Investigar:** "¿Por qué se dispararon los pedidos hace 9 días?"
- **Comparar:** "Mostrame las ventas de esta semana contra la anterior, por categoría."
- **Dashboards:** "Armame un dashboard de ventas de las últimas 4 semanas con un
  filtro de fecha."

Algunas cosas útiles:

- **Cada consulta se puede abrir.** Las herramientas que usó el agente aparecen como
  pasos: abrí uno para ver el SQL, cuántos bytes escaneó y las filas.
- **Modelo y esfuerzo.** Debajo del cuadro de texto elegís con qué modelo responde y
  cuánto razona. Más esfuerzo suele dar mejores respuestas en preguntas difíciles, pero
  tarda y cuesta más. La elección se recuerda en tu navegador.
- **Los turnos siguen solos.** Podés cambiar de chat o cerrar la pestaña: la respuesta
  sigue armándose en el servidor y la ves al volver. "Detener" la corta.
- **Regenerar y calificar.** Debajo de cada respuesta podés pedir otra versión o
  marcarla con 👍 o 👎.
- **Sugerencias.** Al empezar, la app propone preguntas a partir de tus chats, tus
  dashboards y las tablas. Después de cada respuesta, propone cómo seguir.
- **Solo lectura.** El agente nunca modifica el warehouse, y no consulta datasets que
  no estén habilitados.

## Dashboards

Cuando pedís un dashboard, el agente lo arma y se abre al costado del chat. Los
números salen de consultas en vivo: cada vez que alguien lo abre, ve los datos al día.

### Usarlo

- **Filtros.** Al cambiar un filtro, se vuelven a correr solo las consultas que lo
  usan.
- **Refrescar.** Los resultados se guardan en caché un rato (una hora, por defecto).
  "Refrescar" vuelve a consultar.
- **Ver de dónde sale cada número.** Cambiá al modo Datos y hacé clic en un número:
  se abre su fuente, con el SQL, los filtros que usó y el resultado.
- **Problemas.** Si la página tiene un error o un número no coincide con sus datos,
  la barra del visor lo marca. Si el agente acaba de escribir la página, se lo pide
  corregir solo.

### Cambiarlo

- **Con el agente.** Con el dashboard abierto, pedile cambios en el chat: "sumá un
  gráfico de devoluciones por categoría", "este número está mal". El agente sabe qué
  estás mirando: la pestaña, los filtros y el número que seleccionaste.
- **Desde Fuentes.** Si podés editar el dashboard, en Fuentes → Consulta cambiás el
  SQL de una fuente, lo probás, lo guardás y volvés a una versión anterior si hace
  falta.

### Dónde viven

Un dashboard nuevo es personal: solo lo ves vos. Para compartirlo, movelo a un
proyecto (menú del dashboard → Mover a…). Lo verán los miembros del proyecto, según
su rol.

## Base de conocimiento

El panel Conocimiento muestra qué sabe la plataforma de cada tabla: lo que viene de
BigQuery (descripciones, columnas, particiones) y lo que documentó el equipo
(definiciones de métricas, filtros que van siempre, advertencias, joins).

- **Enseñale en el chat.** Si corregís al agente ("los pedidos cancelados no cuentan
  como ventas"), te propone guardarlo y te muestra cómo quedaría. Se guarda solo si lo
  confirmás.
- **Privado o compartido.** Una nota privada la ves solo vos y vale al instante. Lo
  compartido lo ve todo el equipo y queda pendiente hasta que lo apruebe un curador.
- **Correcciones.** Corregir una entrada crea una nueva que reemplaza a la anterior;
  la historia queda.
- **Curadores.** Los curadores y los admins aprueban, rechazan y deprecan lo
  compartido, y pueden volver a sincronizar el catálogo.

Más detalle en [base-de-conocimiento.md](base-de-conocimiento.md).

## Proyectos y permisos

Arriba de la barra lateral elegís el espacio: tu espacio personal o un proyecto.

| Rol | Puede |
| --- | --- |
| owner | Todo lo de editor, más sumar y sacar miembros, cambiar roles, renombrar y archivar el proyecto |
| editor | Editar los dashboards del proyecto (con el agente o desde Fuentes) y compartir chats en él |
| viewer | Ver los dashboards y los chats compartidos |

- **Crear un proyecto.** Desde el selector de espacio. Quien lo crea es owner.
- **Sumar personas.** El owner suma cuentas que ya pueden entrar a la app. Para abrir
  el acceso a alguien nuevo, hablá con un admin.
- **Compartir un chat.** Crea una copia de solo lectura en un proyecto, tal como está
  en ese momento. Tu chat sigue siendo tuyo.
- **Archivar un proyecto.** Sus dashboards vuelven a quien los creó.

## Tu cuenta

- **Permiso de BigQuery.** Si la instalación consulta con la cuenta de cada persona,
  la app te pide el permiso la primera vez. Ves en los datos lo mismo que tu cuenta
  puede leer en BigQuery.
- **Admins.** Desde el menú de usuario → Usuarios: invitar, dar o sacar el rol de
  admin y deshabilitar cuentas.
- **Cerrar sesión.** Desde el menú de usuario.
