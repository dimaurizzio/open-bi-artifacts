# Modelo de seguridad

Qué protege la plataforma, cómo, y qué queda fuera de su alcance. Para reportar una
vulnerabilidad, seguí [SECURITY.md](../SECURITY.md).

## Acceso

- **Solo Google.** Se entra con OpenID Connect, flujo de código con PKCE. El id token
  se verifica completo: firma, audiencia, emisor, vencimiento, `nonce` y
  `email_verified`.
- **Quién entra.** Los mails de `ADMIN_EMAILS` y `ALLOWED_EMAILS`, las cuentas del
  Workspace de `ALLOWED_EMAIL_DOMAINS` y quien invite un admin. Para un dominio se
  exige que Google diga que la cuenta es del Workspace (claim `hd`): no alcanza con que
  el mail termine en ese dominio.
- **Identidad.** El id de usuario es el mail en minúsculas, atado al `sub` de Google: si
  un mail vuelve con otro `sub`, el login se rechaza.
- **Sesiones del lado del servidor.** La cookie `ad_session` es un token al azar
  (HttpOnly, SameSite=Lax, Secure con HTTPS) y la base guarda solo su hash. Dura
  `SESSION_DAYS`. Una cuenta deshabilitada pierde sus sesiones.
- **CSRF.** Además de SameSite, todo pedido que cambia algo tiene que venir del mismo
  origen.
- **Todo cerrado por defecto.** Un middleware exige sesión en todo lo que no es el
  login: la API responde 401 y las páginas redirigen a `/login`.

## Permisos

| Rol en un proyecto | Puede |
| --- | --- |
| owner | Todo lo de editor, más sumar y sacar miembros, cambiar roles, renombrar y archivar |
| editor | Editar los dashboards del proyecto y compartir chats en él |
| viewer | Ver los dashboards y los chats compartidos |

- Un dashboard sin proyecto es personal: solo lo ve y edita quien lo creó.
- Los permisos se fuerzan en el servicio de dashboards, no en la interfaz: valen igual
  para la API, el visor y las herramientas del agente. Un dashboard que no podés ver
  responde como si no existiera.
- Los chats son de cada persona. Compartir un chat crea una copia de solo lectura.
- Un owner solo suma cuentas que ya pueden entrar. Abrir el acceso a un mail nuevo es
  cosa de un admin.

## Datos del warehouse

- **Solo lectura.** El agente no tiene herramientas que escriban, y cada consulta (del
  agente o de un dashboard) pasa por un análisis que rechaza todo lo que no sea una
  sola sentencia de lectura.
- **Datasets habilitados.** Se rechazan las consultas que nombran tablas fuera de
  `WAREHOUSE_DATASETS`. Un alias solo cuenta donde BigQuery lo ve (su bloque y los de
  adentro; en un FROM, solo si se definió antes), así que no puede esconder una tabla.
- **Costo acotado.** Un dry run frena las consultas que escanearían más de
  `MAX_BYTES_SCANNED`.
- **Cada persona ve lo suyo.** Con `QUERY_CREDENTIALS=reader`, el agente y los
  dashboards consultan con el token de Google de quien usa la app, así que nadie ve
  más que lo que su cuenta puede leer. Los refresh tokens se guardan cifrados (Fernet,
  con una clave derivada de `SECRET_KEY`). La caché de resultados es por persona.
- **IAM es la última barrera.** Dale a la cuenta del servidor y a las personas solo los
  roles de lectura que necesitan: *BigQuery Data Viewer* en los datasets, *BigQuery Job
  User* y *MCP Tool User* en el proyecto de ejecución.

## Dashboards

Las páginas las escribe un modelo, así que se tratan como código no confiable.

- **Validación al escribir.** El servidor rechaza páginas con red, almacenamiento,
  scripts externos, iframes, formularios, atributos `on…=` o código que navega a otra
  dirección.
- **Descarte al cargar.** El runtime del visor vuelve a filtrar lo mismo antes de
  montar la página.
- **Aislamiento.** La página corre en un `<iframe sandbox>` sin `allow-same-origin`,
  con una CSP con nonce que bloquea toda conexión de red. No tiene acceso a las
  cookies ni al resto de la app.
- **Datos mediados.** La página no consulta nada por su cuenta: pide una fuente por
  nombre, la app la carga por la API del servidor (con los permisos de quien mira) y se
  la pasa por `postMessage`. La página solo puede pedir las fuentes de su propio
  dashboard: no elige el SQL. Su `call` llega solo a herramientas auxiliares del
  conector (buscar una consulta que quedó corriendo), nunca a una consulta nueva.
- **Lo de cada lector es suyo.** El documento de corridas que escribe la página de cada
  persona (sus filtros, lo que vio) solo lo lee esa persona, aunque otras vean el mismo
  dashboard.
- **Parámetros tipados.** Los valores de los filtros se insertan en el SQL como un
  literal tipado, nunca como texto suelto.

## El agente y la inyección de instrucciones

- El conocimiento, los resultados de consultas y el contenido de los dashboards son
  datos para el agente, no instrucciones.
- Guardar conocimiento exige una confirmación en otro turno: el agente no puede
  guardar nada sin un mensaje de la persona.
- Las guardas SQL y los permisos son código, no instrucciones al modelo: valen aunque
  el modelo se equivoque o lo engañen.

## Secretos

- La configuración va en `.env.local` y `.env.prod`, que git ignora (solo se versionan
  las plantillas `.example`).
- En Docker, la clave de la cuenta de servicio entra como secreto de Compose y el token
  del túnel va en un archivo aparte que la app no ve.
- `SECRET_KEY` firma las cookies y cifra los tokens de Google. Si cambia, se cierran
  todas las sesiones y cada persona vuelve a dar el permiso de BigQuery.

## Despliegue

- El container corre sin privilegios (uid 10001), con el sistema de archivos de solo
  lectura, sin capabilities de Linux y con `no-new-privileges`.
- No abre ningún puerto en el host: el tráfico entra por Cloudflare Tunnel, que
  también pone el HTTPS.

Ver [despliegue.md](despliegue.md).

## Límites conocidos

- **El chequeo de datasets es léxico.** Entiende alias, CTEs y subconsultas, pero no es
  un parser completo de SQL: los permisos de IAM siguen siendo la barrera real.
- **Las vistas se expanden en BigQuery.** Una vista de un dataset habilitado puede leer
  tablas de otros datasets o proyectos; la guarda solo ve el nombre de la vista.
- **Los editores escriben código que corre en el navegador de los lectores.** La
  validación rechaza la navegación evidente, pero es léxica: una página escrita a
  propósito para eso podría mandar a otra dirección lo que muestra, con los datos que
  cada lector ve con su cuenta. Dale el rol de editor solo a personas de confianza.
- **El modelo ve resultados.** Las filas que vuelven de una consulta (hasta
  `AGENT_MAX_ROWS`) y el conocimiento llegan al proveedor del modelo que elijas.
