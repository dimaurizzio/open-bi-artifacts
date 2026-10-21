# Guía para agentes de código

Este archivo es para un agente de código (Claude Code, Codex, Gemini CLI, Cursor u
otro) al que una persona le pidió instalar, desplegar o explicar Open BI Artifacts.
Tu trabajo es dejarlo andando y acompañar a esa persona: hacé vos todo lo que se hace
desde la terminal y decile con precisión lo que solo puede hacer ella.

## Qué es

Un chat con un agente de Google ADK que consulta BigQuery en solo lectura por el
servidor MCP de Google, arma dashboards en vivo y aprende lo que el equipo sabe de sus
datos. FastAPI + React, login con Google, despliegue con Docker y Cloudflare Tunnel.

## Orden de lectura

1. [README.md](README.md): qué hace y la vista general.
2. [docs/primeros-pasos.md](docs/primeros-pasos.md): instalación local completa y
   problemas frecuentes. Es la guía que vas a seguir.
3. [docs/configuracion.md](docs/configuracion.md): todas las variables.
4. Según lo que la persona quiera:
   - datos de ejemplo: [docs/demo.md](docs/demo.md);
   - un servidor: [docs/despliegue.md](docs/despliegue.md).
5. Para explicarle cómo se usa: [docs/guia-de-uso.md](docs/guia-de-uso.md).
6. Si hay que entender o cambiar código: [docs/arquitectura.md](docs/arquitectura.md),
   [docs/seguridad.md](docs/seguridad.md) y [docs/desarrollo.md](docs/desarrollo.md).

## Antes de empezar, preguntá

Hacé estas preguntas juntas, en un solo mensaje:

1. **¿Con qué datos?** La demo (una tienda ficticia, gratis, en el sandbox de
   BigQuery) o su propio warehouse. Si es el suyo: proyecto de facturación y datasets
   que el agente puede leer.
2. **¿Dónde?** En su máquina, o en un servidor con un dominio en Cloudflare.
3. **¿Qué modelo?** Anthropic es el default; también OpenAI o Gemini. Necesita la clave
   del proveedor.
4. **¿Quiénes van a entrar?** Cuentas de un Google Workspace (dominio propio) o de
   Gmail. Cambia cómo se configura el login.

## Quién hace qué

| Lo hacés vos | Lo hace la persona |
| --- | --- |
| Clonar, crear el entorno, instalar dependencias, compilar el front | Iniciar sesión en Google (`gcloud auth …` abre el navegador) |
| Crear `.env.local` o `.env.prod` desde la plantilla y completar lo que no es secreto | Pegar las claves y secretos directamente en el archivo |
| Correr el seed de la demo y cargar su conocimiento | Crear el proyecto de Google Cloud, el cliente OAuth y el túnel de Cloudflare en sus consolas web |
| Levantar la app, leer los logs, diagnosticar | Entrar a la app con su cuenta y dar el permiso de BigQuery |
| Correr los tests | Decidir si algo cuesta plata (facturación, VPS, dominio) |

Para cada paso de consola web, dale la ruta exacta del menú y qué valor poner o
copiar. Las rutas están en las guías.

## Reglas

- **No pidas ni muestres secretos en el chat.** Que la persona pegue las claves de
  modelo, el secreto del cliente OAuth y el token del túnel en el archivo. Si tenés que
  revisar un `.env`, ocultá los valores.
- **Nunca versiones** `.env.local`, `.env.prod` ni otros `.env.*` (salvo las
  plantillas), `data/`, `secrets/` ni credenciales.
- **No modifiques el warehouse de la persona.** La app es de solo lectura. El único
  script que escribe en BigQuery es el seed de la demo, y crea su propio dataset.
- **No desactives las guardas** (SQL de solo lectura, datasets habilitados, límite de
  bytes, permisos) para destrabar algo. Si algo falla, buscá la causa.
- **Preguntá antes** de crear recursos que cuesten plata o de cambiar permisos de IAM.
- **Todo se corre desde la carpeta del repo**, con el entorno virtual activado: la app
  se instala con `pip install -e ".[dev]"` y trae el comando `open-bi` (`open-bi serve`,
  `open-bi demo seed`, `open-bi demo knowledge`, `open-bi backup`).
- **En local, entrá por `http://localhost:8765`**, nunca por `127.0.0.1`: el cliente
  OAuth solo acepta la dirección registrada. Fijá `PUBLIC_URL=http://localhost:8765`.
- **Si la persona ya usa `gcloud` con otra cuenta**, no la pises: usá una configuración
  aparte con `CLOUDSDK_CONFIG` (ver primeros pasos, paso 3).

## Cómo verificar cada etapa

| Etapa | Cómo saber que anda |
| --- | --- |
| Instalación | `pytest` pasa y `npm run build` (en `web/`) termina sin errores |
| Configuración | La app arranca. Si falta algo, el error lista cada variable y para qué sirve |
| Credenciales del servidor | Al arrancar, el catálogo se sincroniza: el panel Conocimiento muestra las tablas |
| Login | La persona entra y ve el saludo con su nombre |
| Permisos de BigQuery | Una pregunta simple ("¿qué tablas hay?") responde con datos |
| Despliegue | `docker compose ps` muestra la app `healthy` y la URL pública abre |
| Servidor en marcha | `GET /healthz` responde 200 |

Si algo falla, empezá por la tabla de problemas frecuentes de
[primeros-pasos.md](docs/primeros-pasos.md#problemas-frecuentes) y por los logs
(`docker compose logs app` en un servidor).

## Cuando quede andando

Guiá a la persona en un primer uso corto:

1. Una pregunta simple, para ver el SQL y la respuesta. Con la demo: "¿Cuánto vendimos
   ayer por canal?"
2. Un dashboard: "Armame un dashboard de ventas de las últimas 4 semanas por canal,
   con un filtro de fecha". Mostrale el modo Datos y el panel Fuentes.
3. Enseñarle algo al agente y ver cómo propone guardarlo en la base de conocimiento.
4. Si es para un equipo: quiénes son admins (`ADMIN_EMAILS`) y curadores
   (`KNOWLEDGE_CURATORS`), y cómo se crean proyectos y se suman personas.

Las ideas de [docs/demo.md](docs/demo.md#ideas-para-los-videos) sirven como recorrido.

## Si te piden cambiar código

Seguí [docs/desarrollo.md](docs/desarrollo.md) y [CONTRIBUTING.md](CONTRIBUTING.md):
el estilo del archivo que tocás, textos en español rioplatense, tests para lo que
cambiás, y la documentación al día en el mismo cambio.
