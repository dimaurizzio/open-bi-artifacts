# Configuración

Todo se configura con variables de entorno, en un archivo por entorno. `APP_ENV`
elige cuál se carga:

| Entorno | `APP_ENV` | Archivo | Plantilla |
| --- | --- | --- | --- |
| Tu máquina | `local` (el default) | `.env.local` | [`.env.local.example`](../.env.local.example) |
| Servidor | `prod` (lo fija `compose.yaml`) | `.env.prod` | [`.env.prod.example`](../.env.prod.example) |

Podés sumar otros entornos con el mismo patrón: `APP_ENV=demo` carga `.env.demo`.
Las variables del sistema (Docker, CI) ganan sobre las del archivo. Los `.env.*` no se
versionan; solo las plantillas.

Al arrancar, la app valida la configuración. Si falta algo obligatorio, no arranca y
lista qué falta y para qué sirve.

## Modelo

| Variable | Default | Qué hace |
| --- | --- | --- |
| `AGENT_MODEL` | `anthropic/claude-haiku-5-5` | Modelo por defecto del chat |
| `AGENT_MODELS` | Solo `AGENT_MODEL` | Modelos que se eligen en el chat: `modelo=Etiqueta`, separados por coma. Se pueden mezclar proveedores |
| `ANTHROPIC_API_KEY` | | Obligatoria si algún modelo es `anthropic/…` |
| `OPENAI_API_KEY` | | Obligatoria si algún modelo es `openai/…` |
| `GEMINI_API_KEY` | | Obligatoria si algún modelo es `gemini/…` (Gemini por LiteLLM) |
| `GOOGLE_API_KEY` | | Para Gemini directo con ADK (nombre sin proveedor, como `gemini-3.8-flash`) |
| `GOOGLE_GENAI_USE_VERTEXAI` | | `TRUE` para usar Gemini directo por Vertex AI en vez de una clave; con `GOOGLE_CLOUD_PROJECT` y `GOOGLE_CLOUD_LOCATION` |
| `MODEL_ROUTES` | `dashboard=anthropic/claude-sonnet-5-5` | Modelo por skill. Solo se usa si activás el routing por skill en `agent.py` (viene desactivado) |

Los nombres siguen el formato de [LiteLLM](https://docs.litellm.ai/docs/providers):
`proveedor/modelo`. La excepción es Gemini directo con ADK, que va sin proveedor. El
esfuerzo que ofrece el chat para cada modelo sale de lo que ese modelo soporta.

Ejemplos:

```bash
# Anthropic: el default, con tres modelos para elegir
AGENT_MODELS=anthropic/claude-haiku-5-5=Haiku 5.5,anthropic/claude-sonnet-5-5=Sonnet 5.5,anthropic/claude-opus-5-5=Opus 5.5

# Gemini directo con ADK como default, y Claude como alternativa
AGENT_MODEL=gemini-3.8-flash
AGENT_MODELS=gemini-3.8-flash=Gemini 3.8 Flash,anthropic/claude-sonnet-5-5=Sonnet 5.5

# OpenAI
AGENT_MODEL=openai/gpt-5
AGENT_MODELS=openai/gpt-5=GPT-5,openai/gpt-5-mini=GPT-5 mini
```

## Warehouse

| Variable | Default | Qué hace |
| --- | --- | --- |
| `WAREHOUSE_PROJECT` | | **Obligatoria.** Proyecto de Google Cloud donde corren y se facturan las consultas |
| `WAREHOUSE_DATASETS` | Sin restricción | Datasets habilitados, `proyecto.dataset` separados por coma. Fuera de estos, las consultas se rechazan |
| `MAX_BYTES_SCANNED` | `21474836480` (20 GiB) | Límite por consulta, controlado con un dry run |
| `AGENT_MAX_ROWS` | `50` | Filas de cada resultado que ve el modelo |
| `BIGQUERY_MCP_URL` | `https://bigquery.googleapis.com/mcp` | Servidor MCP de BigQuery |
| `GOOGLE_APPLICATION_CREDENTIALS` | Las de `gcloud auth application-default login` | Credenciales del servidor: ruta a una clave JSON. En Docker la fija `compose.yaml` |
| `QUERY_CREDENTIALS` | `reader` | `reader`: cada persona consulta con su cuenta. `server`: todos con la cuenta del servidor |

## Login y acceso

| Variable | Default | Qué hace |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` | | **Obligatoria.** Cliente OAuth de tipo "Aplicación web" |
| `GOOGLE_CLIENT_SECRET` | | **Obligatoria.** Secreto de ese cliente |
| `PUBLIC_URL` | La URL desde la que se entra | URL pública, sin `/` al final. **Obligatoria en prod, con https.** En local conviene fijarla en `http://localhost:8765` |
| `ADMIN_EMAILS` | | Admins: entran y administran usuarios desde la app |
| `ALLOWED_EMAIL_DOMAINS` | | Dominios de Google Workspace que pueden entrar |
| `ALLOWED_EMAILS` | | Mails sueltos que pueden entrar (por ejemplo, de Gmail) |
| `SECRET_KEY` | En local, se genera y se guarda en `DATA_DIR/secret_key` | Firma las cookies y cifra los tokens de Google. **Obligatoria en prod**, de 32 caracteres o más |
| `SESSION_DAYS` | `14` | Duración de una sesión |
| `GOOGLE_BIGQUERY_SCOPE` | `https://www.googleapis.com/auth/bigquery` | Permiso que se pide con `QUERY_CREDENTIALS=reader` |

Hace falta al menos uno de `ADMIN_EMAILS`, `ALLOWED_EMAIL_DOMAINS` o
`ALLOWED_EMAILS`: sin ninguno, nadie puede entrar.

Para generar `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Datos

| Variable | Default | Qué hace |
| --- | --- | --- |
| `DATA_DIR` | `data` | Bases, dashboards y caché. Relativa a la carpeta del proyecto. En Docker, `/data` |
| `CHAT_RETENTION_DAYS` | `0` (nunca) | Borra los chats sin actividad en más de N días |
| `DEFAULT_TTL_SECONDS` | `3600` | Vigencia de la caché de las consultas en vivo de los dashboards |

## Base de conocimiento

| Variable | Default | Qué hace |
| --- | --- | --- |
| `KNOWLEDGE_CURATORS` | | Además de los admins, quiénes aprueban conocimiento compartido (mails, coma) |
| `CATALOG_SYNC_HOURS` | `24` | Al arrancar, resincroniza el catálogo si es más viejo que esto (`0` = nunca) |
| `KNOWLEDGE_OKF_DIR` | `knowledge-okf` | Carpeta del bundle OKF. En Docker, `/data/knowledge-okf` |
| `KNOWLEDGE_OKF_AUTO` | `1` | Regenera el bundle OKF al aprobar o deprecar |
| `KB_INDEX_MAX_LINES` | `60` | Líneas del índice de conocimiento que recibe el agente |

## Otros

| Variable | Default | Qué hace |
| --- | --- | --- |
| `APP_ENV` | `local` | Qué archivo `.env.<APP_ENV>` se carga |
| `DEFAULT_USER_ID` | | Solo para migrar datos de una instalación anterior al login (`open-bi migrate <mail>`) |
