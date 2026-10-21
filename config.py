"""Configuración central: todo sale de variables de entorno.

`APP_ENV` elige el entorno (`local` por defecto, `prod` en el servidor) y con él el
archivo que se carga: `.env.local` o `.env.prod`, junto a este archivo. Las
variables que ya vienen del sistema (Docker, CI) ganan sobre las del archivo.
Las plantillas son `.env.local.example` y `.env.prod.example`.

Nada de una instalación en particular vive en el código: lo obligatorio no tiene
valor por defecto y `validate()` frena el arranque del servidor si falta.
"""

import os
import pathlib

from dotenv import load_dotenv

PACKAGE_DIR = pathlib.Path(__file__).parent
APP_ENV = os.getenv("APP_ENV", "local").strip().lower() or "local"
ENV_FILE = PACKAGE_DIR / f".env.{APP_ENV}"
if ENV_FILE.exists():
    load_dotenv(ENV_FILE, override=False)


def _path(name: str, default: pathlib.Path) -> pathlib.Path:
    """Rutas del entorno: relativas a la carpeta del proyecto, no a donde se corre el comando."""
    value = os.getenv(name, "").strip()
    path = pathlib.Path(value) if value else default
    return (path if path.is_absolute() else PACKAGE_DIR / path).resolve()


SKILLS_DIR = PACKAGE_DIR / "skills"
DATA_DIR = _path("DATA_DIR", PACKAGE_DIR / "data")
ARTIFACTS_DIR = DATA_DIR / "artifacts"
SESSIONS_DB = DATA_DIR / "sessions.db"

APP_NAME = "agent_dashboards"

# Modelo por defecto del agente. Formato de LiteLLM, "proveedor/modelo"; cada
# proveedor lee su propia API key (ver _MODEL_KEYS). Por ejemplo:
#   anthropic/claude-haiku-5-5   con ANTHROPIC_API_KEY (el default)
#   openai/gpt-5                 con OPENAI_API_KEY
#   gemini/gemini-3.8-flash      con GEMINI_API_KEY (Google AI Studio)
# Gemini también corre directo con ADK, sin LiteLLM: el nombre va sin proveedor
# ("gemini-3.8-flash") y usa GOOGLE_API_KEY o Vertex AI (ver models.py).
AGENT_MODEL = os.getenv("AGENT_MODEL", "anthropic/claude-haiku-5-5")


def _model_list(raw: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in raw.split(","):
        model, _, label = item.strip().partition("=")
        model = model.strip()
        if model and model not in {m for m, _ in out}:
            out.append((model, label.strip() or model.split("/")[-1]))
    return out


# Modelos que se eligen en el chat, con el effort que soporta cada uno:
# "modelo=Etiqueta" separados por coma, de cualquier proveedor. AGENT_MODEL
# siempre está (es el default); vacío = solo AGENT_MODEL.
AGENT_MODELS = _model_list(os.getenv("AGENT_MODELS", ""))
if AGENT_MODEL not in {m for m, _ in AGENT_MODELS}:
    AGENT_MODELS.insert(0, (AGENT_MODEL, AGENT_MODEL.split("/")[-1]))
# Modelo por skill: al cargar una de estas skills, el resto del turno usa ese
# modelo. Formato: "skill=modelo,skill=modelo". Vacío = siempre AGENT_MODEL.
MODEL_ROUTES = {
    skill.strip(): model.strip()
    for pair in os.getenv(
        "MODEL_ROUTES",
        "dashboard=anthropic/claude-sonnet-5-5",
    ).split(",")
    if "=" in pair
    for skill, model in [pair.split("=", 1)]
    if skill.strip() and model.strip()
}

# Id de usuario de antes del login. Solo lo usa la migración de datos
# (`open-bi migrate`) para pasarlos a una cuenta de Google.
DEFAULT_USER_ID = os.getenv("DEFAULT_USER_ID", "")


def _emails(name: str) -> set[str]:
    return {e.strip().lower() for e in os.getenv(name, "").split(",") if e.strip()}


# --- Login con Google (única forma de acceso) --------------------------------------
# Cliente OAuth "Aplicación web" de Google Cloud. Sin esto nadie puede entrar.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
# URL pública (https://bi.tudominio.com). Vacío = se toma del pedido (detrás
# del túnel hace falta --proxy-headers). La de vuelta es <PUBLIC_URL>/auth/callback.
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
# Quiénes pueden entrar: dominios de Google Workspace y mails sueltos. Los admins
# siempre entran; un admin o el owner de un proyecto puede invitar a otros.
ALLOWED_EMAIL_DOMAINS = _emails("ALLOWED_EMAIL_DOMAINS")
ALLOWED_EMAILS = _emails("ALLOWED_EMAILS")
ADMIN_EMAILS = _emails("ADMIN_EMAILS")
# Clave para firmar cookies y cifrar los tokens de Google. Vacío = se genera una y
# se guarda en DATA_DIR/secret_key (sobrevive reinicios mientras exista el volumen).
SECRET_KEY = os.getenv("SECRET_KEY", "")
SESSION_DAYS = float(os.getenv("SESSION_DAYS", "14"))
# Con qué credenciales corren las consultas al warehouse (agente y dashboards en vivo):
# "reader" = las de cada persona (pide permiso de BigQuery al entrar); "server" = la
# cuenta de servicio para todos (cualquiera que entra ve lo que ve el servidor).
QUERY_CREDENTIALS = os.getenv("QUERY_CREDENTIALS", "reader").strip().lower()
GOOGLE_BIGQUERY_SCOPE = os.getenv("GOOGLE_BIGQUERY_SCOPE", "https://www.googleapis.com/auth/bigquery")
APP_DB = DATA_DIR / "app.db"
# Chats sin actividad en más de estos días se borran (0 = nunca).
CHAT_RETENTION_DAYS = float(os.getenv("CHAT_RETENTION_DAYS", "0"))

# --- Warehouse -------------------------------------------------------------
# Servidor MCP remoto administrado por Google para BigQuery.
BIGQUERY_MCP_URL = os.getenv("BIGQUERY_MCP_URL", "https://bigquery.googleapis.com/mcp")
# Proyecto donde corren (y se facturan) las consultas. Obligatorio.
WAREHOUSE_PROJECT = os.getenv("WAREHOUSE_PROJECT", "").strip()
# Datasets que el agente y los dashboards pueden leer, como "proyecto.dataset".
# Vacío = sin restricción (solo aplica IAM de BigQuery).
WAREHOUSE_DATASETS = [
    d.strip()
    for d in os.getenv("WAREHOUSE_DATASETS", "").split(",")
    if d.strip()
]
# Corte de costo: una consulta que escanee más que esto se rechaza (dry run previo).
MAX_BYTES_SCANNED = int(os.getenv("MAX_BYTES_SCANNED", str(20 * 1024**3)))  # 20 GiB
# Filas que ve el agente en una exploración (el resto se resume).
AGENT_MAX_ROWS = int(os.getenv("AGENT_MAX_ROWS", "50"))
# Herramientas MCP que el agente puede usar (nunca execute_sql, que escribe).
AGENT_MCP_TOOLS = [
    "list_dataset_ids",
    "get_dataset_info",
    "list_table_ids",
    "get_table_info",
    "execute_sql_readonly",
    "get_query_results",
]
# Herramienta permitida por conector para las fuentes de un artifact (forzada).
CONNECTOR_TOOLS = {"bigquery": {"execute_sql_readonly"}}

# --- Artifacts ---------------------------------------------------------------
MAX_DOC_BYTES = 256 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
DEFAULT_TTL_SECONDS = int(os.getenv("DEFAULT_TTL_SECONDS", "3600"))
MAX_RUN_RECORDS = 50

# --- Conocimiento --------------------------------------------------------------
# Base propia: catálogo de BigQuery, conocimiento compartido y notas privadas.
KNOWLEDGE_DB = DATA_DIR / "knowledge.db"
# Quiénes aprueban o rechazan lo compartido, por mail (los admins también pueden).
KNOWLEDGE_CURATORS = _emails("KNOWLEDGE_CURATORS")
# Cada cuántas horas se resincroniza el catálogo al arrancar el servidor (0 = nunca solo).
CATALOG_SYNC_HOURS = float(os.getenv("CATALOG_SYNC_HOURS", "24"))
# Bundle OKF con lo compartido y aprobado; se regenera al aprobar, deprecar o sincronizar.
KNOWLEDGE_OKF_DIR = _path("KNOWLEDGE_OKF_DIR", PACKAGE_DIR / "knowledge-okf")
KNOWLEDGE_OKF_AUTO = os.getenv("KNOWLEDGE_OKF_AUTO", "1") not in ("0", "false", "no", "")
# Líneas del índice corto que se inyecta en la skill ({kb_index}).
KB_INDEX_MAX_LINES = int(os.getenv("KB_INDEX_MAX_LINES", "60"))


# --- Validación ----------------------------------------------------------------------


# Clave que pide cada proveedor de LiteLLM, por prefijo del modelo.
_MODEL_KEYS = {
    "anthropic/": ("ANTHROPIC_API_KEY", "la clave de la API de Anthropic (o cambiá AGENT_MODEL a otro proveedor)."),
    "openai/": ("OPENAI_API_KEY", "la clave de la API de OpenAI."),
    "gemini/": ("GEMINI_API_KEY", "la clave de Google AI Studio para usar Gemini con LiteLLM."),
}


def _missing_model_keys() -> list[tuple[str, str]]:
    """Claves que faltan para los modelos de AGENT_MODELS (una vez por proveedor)."""
    out: dict[str, str] = {}
    for model in [AGENT_MODEL, *(m for m, _ in AGENT_MODELS)]:
        for prefix, (key, why) in _MODEL_KEYS.items():
            if model.startswith(prefix) and not os.getenv(key):
                out[key] = why
        native_gemini = "/" not in model and model.startswith("gemini")
        if native_gemini and not (os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_GENAI_USE_VERTEXAI")):
            out["GOOGLE_API_KEY"] = "la clave de Google AI Studio para Gemini directo con ADK (o GOOGLE_GENAI_USE_VERTEXAI=TRUE)."
    return list(out.items())


class ConfigError(RuntimeError):
    """Falta configuración obligatoria o tiene un valor inválido."""


def problems() -> list[str]:
    """Lo que impide arrancar el servidor con esta configuración (vacío = todo bien)."""
    out = []
    if not WAREHOUSE_PROJECT:
        out.append("WAREHOUSE_PROJECT: el proyecto de Google Cloud donde corren y se facturan las consultas.")
    for key, why in _missing_model_keys():
        out.append(f"{key}: {why}")
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        out.append("GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET: el cliente OAuth de Google (el login es la única forma de entrar).")
    if not (ALLOWED_EMAIL_DOMAINS or ALLOWED_EMAILS or ADMIN_EMAILS):
        out.append("ADMIN_EMAILS (y ALLOWED_EMAIL_DOMAINS o ALLOWED_EMAILS): sin ninguno, nadie puede entrar.")
    if QUERY_CREDENTIALS not in ("reader", "server"):
        out.append(f"QUERY_CREDENTIALS: es 'reader' o 'server', no {QUERY_CREDENTIALS!r}.")
    if APP_ENV == "prod":
        if not PUBLIC_URL.startswith("https://"):
            out.append("PUBLIC_URL: en prod es obligatoria y con https (https://bi.tudominio.com).")
        if len(SECRET_KEY) < 32:
            out.append("SECRET_KEY: en prod es obligatoria, de 32 caracteres o más "
                       "(python -c \"import secrets; print(secrets.token_urlsafe(48))\").")
    return out


def validate() -> None:
    found = problems()
    if found:
        where = (
            ENV_FILE.name if ENV_FILE.exists()
            else f"las variables de entorno (no hay {ENV_FILE.name}; la plantilla es {ENV_FILE.name}.example)"
        )
        raise ConfigError(f"Configuración incompleta para APP_ENV={APP_ENV}, en {where}:\n- " + "\n- ".join(found))
