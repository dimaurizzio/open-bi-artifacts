# Primeros pasos

Cómo levantar la app en tu máquina, desde cero. Si solo querés probarla con datos de
ejemplo, seguí esta guía y en el paso 2 usá la [demo](demo.md).

## Qué necesitás

- **Python 3.13** y **Node 20** o más nuevo (Node solo compila el front).
- El **SDK de Google Cloud** (`gcloud` y `bq`).
- Una **cuenta de Google** y un proyecto de Google Cloud con BigQuery. Alcanza con el
  [sandbox de BigQuery](https://cloud.google.com/bigquery/docs/sandbox), sin
  facturación.
- Una **clave de modelo**: por defecto, la de Anthropic. También sirven OpenAI o Gemini
  (ver [configuración](configuracion.md#modelo)).

## 1. Instalar

El código es un paquete de Python llamado `agent_dashboards`: la carpeta tiene que
llamarse así, y los comandos de Python se corren desde la carpeta de arriba.

```bash
mkdir open-bi-artifacts && cd open-bi-artifacts
```

```bash
git clone https://github.com/dimaurizzio/open-bi-artifacts.git agent_dashboards
```

```bash
python -m venv .venv
```

Activá el entorno (`source .venv/bin/activate`, o `.venv\Scripts\activate` en
Windows) e instalá las dependencias:

```bash
pip install -r agent_dashboards/requirements-dev.txt
```

```bash
cd agent_dashboards/web && npm ci && npm run build && cd ../..
```

## 2. Elegir los datos

- **Tu warehouse:** anotá el proyecto donde se van a facturar las consultas y los
  datasets que el agente puede leer (`proyecto.dataset`).
- **La demo:** crea una tienda online ficticia en tu proyecto, gratis. Seguí
  [demo.md](demo.md) y volvé acá.

La cuenta con la que corrés la app necesita, en el proyecto de ejecución, los roles
*BigQuery Job User* y *MCP Tool User* (`roles/mcp.toolUser`), y *BigQuery Data Viewer*
en los datasets. Si creaste el proyecto, ya los tenés.

## 3. Credenciales del servidor

La app usa las credenciales de tu máquina para sincronizar el catálogo y generar
sugerencias:

```bash
gcloud auth application-default login
```

```bash
gcloud auth application-default set-quota-project TU_PROYECTO
```

> Si en tu máquina ya usás `gcloud` con otra cuenta y no querés pisarla, usá una
> configuración aparte: `export CLOUDSDK_CONFIG=~/.gcloud-open-bi` (en PowerShell,
> `$env:CLOUDSDK_CONFIG="$HOME\.gcloud-open-bi"`) antes de los comandos de arriba, y
> después apuntá `GOOGLE_APPLICATION_CREDENTIALS` a
> `~/.gcloud-open-bi/application_default_credentials.json`.

## 4. Cliente de Google para el login

El login con Google es la única forma de entrar. En la
[consola de Google Cloud](https://console.cloud.google.com/auth/overview), en el mismo
proyecto u otro:

1. **Branding y público.** Si todos los usuarios son de tu Google Workspace, elegí
   *Interno*: no hace falta que Google revise la app. Con cuentas de Gmail, elegí
   *Externo* y sumá a cada persona como usuario de prueba.
2. **Acceso a datos.** Agregá los permisos `openid`, `email`, `profile` y
   `https://www.googleapis.com/auth/bigquery`.
3. **Clientes → Crear cliente →** *Aplicación web*, con la URI de redireccionamiento
   `http://localhost:8765/auth/callback`.
4. Copiá el ID y el secreto del cliente.

## 5. Configurar

```bash
cp agent_dashboards/.env.local.example agent_dashboards/.env.local
```

Completá en `agent_dashboards/.env.local`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
WAREHOUSE_PROJECT=tu-proyecto
WAREHOUSE_DATASETS=tu-proyecto.tu_dataset
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
PUBLIC_URL=http://localhost:8765
ADMIN_EMAILS=vos@tudominio.com
```

Todas las variables están en [configuracion.md](configuracion.md).

## 6. Arrancar

Desde la carpeta de arriba del proyecto:

```bash
python -m uvicorn agent_dashboards.server.app:app --port 8765
```

Abrí **http://localhost:8765** (no `127.0.0.1`: el cliente de Google solo acepta la
dirección que registraste) y entrá con tu cuenta.

- Con `QUERY_CREDENTIALS=reader` (el default), la app te pide el permiso de BigQuery la
  primera vez. Si no lo diste, aparece un aviso con "Dar permiso".
- Al arrancar, la app sincroniza el catálogo: en el panel Conocimiento vas a ver las
  tablas de los datasets habilitados.

Para trabajar en el front con recarga en vivo, dejá el backend corriendo y en otra
terminal:

```bash
cd agent_dashboards/web && npm run dev
```

Queda en http://localhost:5173, con proxy a la API.

## Problemas frecuentes

| Síntoma | Causa y solución |
| --- | --- |
| La app no arranca y lista variables | Falta configuración obligatoria. Completá lo que dice y volvé a arrancar |
| Google muestra `redirect_uri_mismatch` | Entraste por una dirección que no está registrada en el cliente OAuth (por ejemplo, `127.0.0.1`). Entrá por `http://localhost:8765` o fijá `PUBLIC_URL` |
| "Acceso bloqueado" al elegir una cuenta de Gmail | El cliente es *Interno* o la cuenta no es usuario de prueba |
| El catálogo queda vacío con `Invalid project ID` | `WAREHOUSE_DATASETS` tiene que ser `proyecto.dataset`, no solo el dataset |
| Las consultas fallan con `Access Denied` | Faltan roles: *BigQuery Job User* y *MCP Tool User* en el proyecto, *BigQuery Data Viewer* en los datasets |
| Aviso "falta tu permiso" de BigQuery | Hacé clic en "Dar permiso", o revisá que el cliente OAuth tenga el permiso de BigQuery |
| El dashboard no se ve | Algunos navegadores embebidos bloquean los iframes aislados. Probá en Chrome, Edge o Firefox |
| `address already in use` | Ya hay otra instancia en el puerto 8765 |

## Siguientes pasos

- [Guía de uso](guia-de-uso.md): qué podés pedirle y cómo se usan los dashboards.
- [Despliegue](despliegue.md): en un servidor, con Docker y Cloudflare Tunnel.
