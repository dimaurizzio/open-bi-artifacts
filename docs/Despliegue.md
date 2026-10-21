# Despliegue en un VPS con Docker y Cloudflare Tunnel

Para probar en tu máquina sin Docker, empezá por [primeros-pasos.md](primeros-pasos.md).
Todas las variables están en [configuracion.md](configuracion.md) y el modelo de
seguridad, en [seguridad.md](seguridad.md).

La app corre en un container sin ningún puerto abierto en el VPS. `cloudflared`,
en otro container, abre la conexión hacia Cloudflare y le pasa el tráfico a la
app por la red interna de Docker. Cloudflare pone el HTTPS.

```
Internet ──HTTPS──▶ Cloudflare ◀──túnel saliente── cloudflared ──http://app:8765──▶ app
                                                    (container)                (container)
```

## Qué hay en el repo

| Archivo | Para qué |
| --- | --- |
| `Dockerfile` | Imagen en tres etapas: build del front (Node), dependencias de Python en un venv, imagen final mínima |
| `compose.yaml` | Producción: `app` + `cloudflared`, endurecidos |
| `compose.local.yaml` | Agrega `127.0.0.1:8765` para probar la imagen en tu máquina sin túnel |
| `.env.prod.example` | Variables de producción (copiar a `.env.prod`); `.env.local.example` es la de tu máquina |
| `.dockerignore` | Deja afuera secretos, datos y builds locales |
| `server/backup.py` | Backup en caliente de `/data` |

La imagen:

- Corre como usuario sin privilegios (uid 10001), con el sistema de archivos de
  solo lectura. Solo se escribe en `/data` (volumen) y en `/tmp` (en memoria).
- No tiene capabilities de Linux y no puede ganar privilegios (`no-new-privileges`).
- Tiene healthcheck (`/healthz`); `cloudflared` espera a que la app esté sana.
- Corre con un solo worker de uvicorn: los turnos en curso viven en memoria del
  proceso (`server/runs.py`). Para más workers hace falta un broker (Valkey).
- Rota los logs (5 × 10 MB por container).

## Requisitos

- Un VPS con Linux (Debian 12 o Ubuntu 24.04) y Docker Engine con el plugin
  Compose (`docker compose version`), instalado desde el repositorio oficial de Docker.
- Un dominio con el DNS en Cloudflare (cuenta gratis).
- Una cuenta de servicio de Google Cloud con acceso de lectura al warehouse:
  *BigQuery Data Viewer* en los datasets habilitados, y *BigQuery Job User* y
  *MCP Tool User* (`roles/mcp.toolUser`) en el proyecto de ejecución. Bajá su
  clave JSON. La app la usa para sincronizar el catálogo; las consultas de cada
  persona van con su propia cuenta (ver "Login con Google").
- La API key del modelo: Anthropic por defecto, u OpenAI o Gemini (ver la sección
  Modelo de `.env.prod.example`).

## Pasos

1. **Copiar el proyecto** al VPS (`git clone` o `rsync`) y entrar a la carpeta.

2. **Configurar la app:**

   ```bash
   cp .env.prod.example .env.prod
   ```

   Completá lo obligatorio: la clave del modelo, `WAREHOUSE_PROJECT`,
   `WAREHOUSE_DATASETS`, lo del login (ver "Login con Google" más abajo),
   `PUBLIC_URL` con https y `SECRET_KEY`:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Si falta algo, la app no arranca y `docker compose logs app` dice qué.

3. **Credenciales de Google:** copiá la clave a `secrets/gcp-service-account.json`
   y dejala legible solo para el usuario de la app:

   ```bash
   sudo chown 10001:10001 secrets/gcp-service-account.json
   ```

   ```bash
   sudo chmod 400 secrets/gcp-service-account.json
   ```

4. **Crear el túnel** en Cloudflare: Zero Trust → Networks → Tunnels → Create a
   tunnel → *Cloudflared*. Copiá el token y guardalo en `secrets/cloudflared.env`:

   ```
   TUNNEL_TOKEN=eyJ...
   ```

   En el mismo túnel, agregá un *Public hostname*: `bi.tudominio.com` →
   servicio `http://app:8765`. Va `app` (el nombre del servicio en la red de Docker),
   no `localhost`.

5. **Levantar:**

   ```bash
   docker compose up -d --build
   ```

   ```bash
   docker compose ps
   ```

   La app tiene que figurar como `healthy` y la URL tiene que abrir.

6. **Cerrar el firewall.** Ningún servicio necesita puertos de entrada:

   ```bash
   sudo ufw default deny incoming
   ```

   ```bash
   sudo ufw allow OpenSSH
   ```

   ```bash
   sudo ufw enable
   ```

   Para cerrar también SSH, primero confirmá que podés entrar por la consola web
   del proveedor o por SSH a través del túnel (Cloudflare Access).

## Login con Google

Es la única forma de entrar: sin cliente de Google configurado, la app no arranca.
Paso a paso en [primeros-pasos.md](primeros-pasos.md#4-cliente-de-google-para-el-login).

1. **Pantalla de consentimiento** (Google Cloud Console → Google Auth Platform →
   Branding / Audience). Si todos son de tu Google Workspace, elegí *Interna*: no
   hace falta que Google revise la app.
2. **Permisos (Data Access):** `openid`, `email`, `profile` y, con
   `QUERY_CREDENTIALS=reader` (el default), `https://www.googleapis.com/auth/bigquery`.
3. **Cliente OAuth** (Clients → Create client → *Aplicación web*), con estas URIs
   de redireccionamiento:
   - `https://bi.tudominio.com/auth/callback`
   - `http://localhost:8765/auth/callback` (para desarrollo)
4. En `.env.prod` (y en `.env.local` con el cliente de desarrollo): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `PUBLIC_URL`,
   `ALLOWED_EMAIL_DOMAINS` (o `ALLOWED_EMAILS`) y `ADMIN_EMAILS`.

### Quién entra

- Los mails de `ADMIN_EMAILS`, que además administran usuarios desde la app.
- Las cuentas del Workspace de `ALLOWED_EMAIL_DOMAINS`. Se exige que Google diga
  que la cuenta es del Workspace (claim `hd`), no alcanza con que el mail termine
  en el dominio.
- Los mails de `ALLOWED_EMAILS`.
- Quien invite un admin (menú de usuario → Usuarios) o el owner de un proyecto
  (solo cuentas que ya cumplen lo anterior).

Una cuenta deshabilitada no entra y se le cierran las sesiones.

### Consultas con la cuenta de cada persona

Con `QUERY_CREDENTIALS=reader`, el agente y los dashboards en vivo consultan
BigQuery con el token de quien está usando la app: cada persona ve lo que su
cuenta puede leer. Para eso cada usuario necesita en el proyecto de ejecución
*BigQuery Job User* y *MCP Tool User*, y *BigQuery Data Viewer* en los datasets.
El permiso de BigQuery se pide al entrar; el refresh token se guarda cifrado con
`SECRET_KEY`. La caché de los dashboards es por persona. La cuenta de servicio
solo se usa para sincronizar el catálogo y para las sugerencias.

Con `QUERY_CREDENTIALS=server`, todos consultan con la cuenta de servicio:
cualquiera que entra ve lo que ve el servidor.

## Operación

| Tarea | Comando |
| --- | --- |
| Ver logs | `docker compose logs -f app` |
| Actualizar | `git pull` y `docker compose up -d --build` |
| Backup | `docker compose exec app python -m agent_dashboards.server.backup /data/backups` |
| Sacar un backup del container | `docker compose cp app:/data/backups ./backups` |
| Reiniciar | `docker compose restart app` |

El backup copia las bases SQLite con la API de backup de SQLite (consistente con
la app andando), los artifacts y el bundle OKF, y conserva los últimos 14. Para
hacerlo todas las noches, un cron del VPS:

```
0 3 * * * cd /ruta/al/proyecto && docker compose exec -T app python -m agent_dashboards.server.backup /data/backups
```

Guardá una copia fuera del VPS: un backup en el mismo disco no protege de perder
el disco.

## Probar la imagen en tu máquina

Con `.env.local` (cliente de Google de desarrollo, `PUBLIC_URL` vacío) y las
credenciales de `gcloud auth application-default login`, sin túnel:

```bash
APP_ENV=local GCP_CREDENTIALS_FILE=~/.config/gcloud/application_default_credentials.json docker compose -f compose.yaml -f compose.local.yaml up --build app
```

En Windows el archivo está en `%APPDATA%\gcloud\application_default_credentials.json`.
La app queda en http://localhost:8765. Para desarrollar sin Docker, alcanza con
`.env.local`: `APP_ENV` es `local` por defecto.

## Por qué no se corta el chat detrás de Cloudflare

Cloudflare corta una conexión que pasa unos 100 segundos sin mandar datos. El
chat es un stream SSE que puede quedar callado mientras corre una consulta larga,
así que el servidor manda un comentario `: ping` cada 20 segundos
(`server/runs.py`, `sse_events`).
