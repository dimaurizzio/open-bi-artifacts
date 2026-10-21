# Seguridad

## Cómo reportar una vulnerabilidad

No abras un issue público. Usá el reporte privado de vulnerabilidades de GitHub
(pestaña *Security* → *Report a vulnerability*) con:

- qué encontraste y dónde (archivo, endpoint);
- cómo reproducirlo;
- qué impacto creés que tiene.

Te respondemos apenas lo veamos y coordinamos la publicación del arreglo.

## Qué está dentro del alcance

- Saltear el login o los permisos de proyectos y dashboards.
- Leer datos de BigQuery que tu cuenta no puede leer, o escribir en el warehouse.
- Escapar del sandbox del visor de dashboards (red, cookies, el resto de la app).
- Que el agente ejecute instrucciones escondidas en datos, conocimiento o páginas.
- Filtrar secretos: tokens de Google, `SECRET_KEY`, claves de la API.

## Cómo está protegido

El modelo de seguridad (guardas SQL, CSP del visor, sesiones, credenciales por
persona) está en [docs/seguridad.md](docs/seguridad.md).
