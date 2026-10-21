"""Servidor de la app: chat con el agente (ADK Runner + SSE), API de artifacts y visor.

Correr desde C:\\dev\\adk-workspace:

    .venv\\Scripts\\python -m uvicorn agent_dashboards.server.app:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from google.adk.agents.run_config import RunConfig, StreamingMode  # noqa: E402
from google.adk.events import Event, EventActions  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions.sqlite_session_service import SqliteSessionService  # noqa: E402
from google.genai import types  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from .. import config  # noqa: E402
from .. import models as model_choice  # noqa: E402
from ..user_context import turn_context  # noqa: E402

# El entorno (.env.local o .env.prod) lo carga config. Sin lo obligatorio no se arranca.
config.validate()
from ..agent import root_agent  # noqa: E402
from ..artifacts.dashboard import DashError, now_iso  # noqa: E402
from ..artifacts.dashboard import service as dash  # noqa: E402
from ..artifacts.service import ArtifactError, service  # noqa: E402
from ..auth import google  # noqa: E402
from ..auth.store import AppStore  # noqa: E402
from ..knowledge.service import service as knowledge  # noqa: E402
from ..warehouse import credentials, sql_guard  # noqa: E402
from . import retention, suggestions  # noqa: E402
from .auth_api import AuthContext, current_user  # noqa: E402
from .auth_api import install as install_auth  # noqa: E402
from .knowledge_api import build_router as knowledge_router  # noqa: E402
from .runs import ChatRun, runs, sse_events  # noqa: E402

STATIC = Path(__file__).parent / "static"
TOKENS = json.loads((Path(__file__).resolve().parents[1] / "artifacts" / "tokens.json").read_text(encoding="utf-8"))

config.DATA_DIR.mkdir(parents=True, exist_ok=True)
session_service = SqliteSessionService(str(config.SESSIONS_DB))
runner = Runner(app_name=config.APP_NAME, agent=root_agent, session_service=session_service)

logger = logging.getLogger("agent_dashboards.server")


async def _sync_catalog_if_stale() -> None:
    """Al arrancar: resincroniza el catálogo de BigQuery si nunca se hizo o ya es viejo."""
    try:
        age = knowledge.catalog_age_hours()
        if age is None or age >= config.CATALOG_SYNC_HOURS:
            await knowledge.sync_catalog()
    except Exception:  # noqa: BLE001 - sin catálogo la app funciona igual
        logger.exception("No se pudo sincronizar el catálogo")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks = [asyncio.create_task(retention.loop(session_service, auth.store, runs.running_ids))]
    if config.CATALOG_SYNC_HOURS > 0:
        tasks.append(asyncio.create_task(_sync_catalog_if_stale()))
    yield
    for task in tasks:
        if not task.done():
            task.cancel()


app = FastAPI(title="Agent Dashboards", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# --- cuentas, permisos y credenciales -------------------------------------------------------
auth = AuthContext(AppStore())
dash.can_view = auth.access.can_view
dash.can_edit = auth.access.can_edit
dash.client_for = credentials.client_for
dash.per_user_cache = credentials.per_user
knowledge.extra_curator = auth.access.is_admin
if google.configured() and google.reader_mode():
    credentials.set_token_provider(auth.broker.access_token)


async def _chat_snapshot(user: str, chat_id: str) -> tuple[str, list[dict[str, Any]]] | None:
    s = await session_service.get_session(app_name=config.APP_NAME, user_id=user, session_id=chat_id)
    if not s:
        return None
    events = [e for e in _visible_events(s.events) if not e.partial]
    return s.state.get("chat_title") or "Chat", [i for e in events for i in _event_items(e)]


install_auth(app, auth, dash=dash, chat_snapshot=_chat_snapshot, is_curator=knowledge.is_curator)
app.include_router(knowledge_router(current_user))


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Healthcheck del container: el proceso responde y el directorio de datos se puede escribir."""
    ok = config.DATA_DIR.is_dir() and os.access(config.DATA_DIR, os.W_OK)
    return JSONResponse({"ok": ok}, status_code=200 if ok else 503)


# ---------------------------------------------------------------- páginas


SHELL = STATIC / "app" / "index.html"  # build de web/ (React + Vite)


def _shell() -> FileResponse:
    if not SHELL.exists():
        raise HTTPException(503, "Falta el build del frontend: cd web && npm install && npm run build")
    return FileResponse(SHELL)


@app.get("/")
async def index():
    return _shell()


@app.get("/a/{artifact_id}")
async def artifact_page(artifact_id: str, request: Request):
    """Artifact a pantalla completa (mismo shell en modo solo, sin chat)."""
    _require_artifact_viewer(artifact_id, current_user(request))
    return _shell()


def _require_artifact_viewer(artifact_id: str, user: str) -> None:
    """404 si no existe o si la persona no lo puede ver (no se distingue un caso del otro)."""
    try:
        if dash.store.is_v2(artifact_id):
            dash.require_viewer(artifact_id, user)
        else:
            _legacy_owner(artifact_id, user)
    except (DashError, ArtifactError) as e:
        raise HTTPException(404, "No existe el artifact.") from e


def _legacy_owner(artifact_id: str, user: str) -> dict[str, Any]:
    """Artifacts del contrato 1: personales, solo los ve quien los creó."""
    meta = service.require(artifact_id)
    if meta.get("owner") != user:
        raise ArtifactError(f"No existe el artifact {artifact_id}.")
    return meta


def _tokens_css() -> str:
    def block(mode: str) -> str:
        t = TOKENS[mode]
        lines = [f"--dash-{k}: {v};" for k, v in t.items() if isinstance(v, str)]
        lines += [f"--dash-s{i + 1}: {c};" for i, c in enumerate(t["series"])]
        return " ".join(lines)

    status = " ".join(f"--dash-{k}: {v};" for k, v in TOKENS["status"].items())
    font = f"--dash-font: {TOKENS['font']};"
    return (
        f":root {{ color-scheme: light; {block('light')} {status} {font} }}\n"
        f"@media (prefers-color-scheme: dark) {{ :root:not([data-theme=\"light\"]) {{ color-scheme: dark; {block('dark')} }} }}\n"
        f":root[data-theme=\"dark\"] {{ color-scheme: dark; {block('dark')} }}\n"
    )


def _script_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


_CSP = (
    "default-src 'none'; "
    "script-src 'nonce-{nonce}'; "
    "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; "
    "connect-src 'none'; frame-src 'none'; worker-src 'none'; form-action 'none'; "
    "base-uri 'none'; frame-ancestors 'self'"
)


def _viewer_v2(artifact_id: str, theme: str) -> HTMLResponse:
    """Visor del contrato 2: tokens del tema, d3 y el runtime; la página la monta el runtime."""
    platform = dash.require(artifact_id)
    datasets = dash.store.list_docs(artifact_id, "datasets")
    files = dash.store.list_docs(artifact_id, "files")
    cfg = {
        "id": artifact_id,
        "contract": 2,
        "title": (dash.store.get(artifact_id, "dash", "meta") or {}).get("title", ""),
        "params": dash.params_defaults(artifact_id),
        "datasets": {
            ds_id: {
                "kind": "file" if (d.get("source") or {}).get("kind") == "file" else (d.get("source") or {}).get("kind", "query"),
                "paused": isinstance(d.get("request"), dict),
                "params": sql_guard.params_in_args(((d.get("source") or {}).get("args")) or {}),
            }
            for ds_id, d in datasets.items()
        },
        "files": {name: f.get("text", "") for name, f in files.items()},
        "updated": platform.get("updated_at"),
    }
    nonce = secrets.token_urlsafe(18)
    html = f"""<!doctype html>
<html lang="es" data-theme="{'dark' if theme == 'dark' else 'light'}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{(STATIC / "viewer-v2.css").read_text(encoding="utf-8")}</style>
<script nonce="{nonce}">{(STATIC / "vendor" / "d3.v7.min.js").read_text(encoding="utf-8")}</script>
<script nonce="{nonce}">window.__DASH__ = {_script_json(cfg)};</script>
</head><body><div id="dash-root" data-dash-content></div><div id="dash-stopped" role="status" hidden>El código de esta página se detuvo</div>
<script nonce="{nonce}">{(STATIC / "viewer-v2.js").read_text(encoding="utf-8")}</script>
</body></html>"""
    return HTMLResponse(html, headers={"Content-Security-Policy": _CSP.format(nonce=nonce), "Cache-Control": "no-store"})


@app.get("/viewer/{artifact_id}")
async def viewer(artifact_id: str, request: Request, theme: str = "light"):
    """Visor fijo. Se carga en un iframe sandbox (sin allow-same-origin) y sin red."""
    _require_artifact_viewer(artifact_id, current_user(request))
    if dash.store.is_v2(artifact_id):
        try:
            return _viewer_v2(artifact_id, theme)
        except DashError as e:
            raise HTTPException(404, str(e)) from e
    try:
        d = service.describe(artifact_id, include_page=True)
    except ArtifactError as e:
        raise HTTPException(404, str(e)) from e
    nonce = secrets.token_urlsafe(18)
    cfg = {
        "id": artifact_id,
        "meta": d["meta"],
        "params": d["params"],
        "datasets": {
            k: {f: v.get(f) for f in ("id", "title", "mode", "params", "columns")} for k, v in d["datasets"].items()
        },
        "tokens": TOKENS,
        "locale": "es-AR",
        "page": d["page"],
    }
    theme = "dark" if theme == "dark" else "light"
    html = f"""<!doctype html>
<html lang="es" data-theme="{theme}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{_tokens_css()}{(STATIC / "viewer.css").read_text(encoding="utf-8")}</style>
<script nonce="{nonce}">{(STATIC / "vendor" / "d3.v7.min.js").read_text(encoding="utf-8")}</script>
<script nonce="{nonce}">window.__DASH__ = {_script_json(cfg)};</script>
</head><body><div id="dash-filters"></div><main id="dash-root"></main><div id="dash-status" hidden></div>
<script nonce="{nonce}">{(STATIC / "viewer-runtime.js").read_text(encoding="utf-8")}</script>
</body></html>"""
    csp = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; "
        "connect-src 'none'; frame-src 'none'; worker-src 'none'; form-action 'none'; "
        "base-uri 'none'; frame-ancestors 'self'"
    )
    return HTMLResponse(html, headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"})


# ---------------------------------------------------------------- artifacts


class DataRequest(BaseModel):
    params: dict[str, Any] = {}
    cache: str = "prefer"


def _err(e: ArtifactError, status: int = 400) -> HTTPException:
    return HTTPException(status, str(e))


@app.get("/api/artifacts")
async def list_artifacts(request: Request, project: str | None = None):
    """Los artifacts personales (sin proyecto) o, con ?project=<id>, los de ese proyecto."""
    user = current_user(request)
    if project:
        if auth.access.project_role(project, user) is None:
            raise HTTPException(404, "No existe el proyecto o no sos miembro.")
        mine = [d for d in dash.store.list() if d.get("project_id") == project]
        legacy: list[dict[str, Any]] = []
    else:
        mine = [d for d in dash.store.list(owner=user) if not d.get("project_id")]
        legacy = service.store.list(owner=user)
    v2 = [
        {"id": d["id"], "kind": "dashboard", "contract": 2, "title": d["title"], "description": "", "owner": d["owner"],
         "project_id": d.get("project_id"), "editable": dash.can_edit(d, user),
         "created_at": d["created_at"], "updated_at": d["updated_at"]}
        for d in mine
    ]
    return sorted(v2 + legacy, key=lambda m: m.get("updated_at", ""), reverse=True)


@app.get("/api/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, request: Request):
    if dash.store.is_v2(artifact_id):
        try:
            return dash.describe(artifact_id, current_user(request))
        except DashError as e:
            raise HTTPException(404, str(e)) from e
    try:
        _legacy_owner(artifact_id, current_user(request))
        d = service.describe(artifact_id, include_page=True)
    except ArtifactError as e:
        raise _err(e, 404) from e
    d["runs"] = service.runs_summary(artifact_id, last=10)
    return d


@app.post("/api/artifacts/{artifact_id}/data/{dataset_id}")
async def get_data(artifact_id: str, dataset_id: str, body: DataRequest, request: Request):
    """La plataforma media la consulta: la página nunca ve credenciales ni SQL arbitrario."""
    user = current_user(request)
    try:
        _legacy_owner(artifact_id, user)
    except ArtifactError as e:
        raise _err(e, 404) from e
    specs = service.param_specs(artifact_id)
    values = {k: v for k, v in body.params.items() if k in specs}
    cache = body.cache if body.cache in ("prefer", "auto", "refresh") else "prefer"
    try:
        return await service.get_data(artifact_id, dataset_id, values, cache, warehouse=credentials.client_for(user))
    except ArtifactError as e:
        raise _err(e) from e


@app.post("/api/artifacts/{artifact_id}/runs")
async def post_run(artifact_id: str, request: Request):
    body = await request.json()
    try:
        _legacy_owner(artifact_id, current_user(request))
        service.record_run(artifact_id, body.get("record", {}), viewer=current_user(request))
    except ArtifactError as e:
        raise _err(e, 404) from e
    return {"ok": True}


@app.post("/api/artifacts/{artifact_id}/files")
async def upload(artifact_id: str, request: Request, file: UploadFile = File(...)):
    raw = await file.read()
    try:
        _legacy_owner(artifact_id, current_user(request))
        return service.attach_file(artifact_id, file.filename or "archivo.csv", raw, current_user(request))
    except (ArtifactError, ValueError, UnicodeDecodeError) as e:
        raise HTTPException(400, str(e)) from e


# ---------------------------------------------------------------- dashboards (contrato 2)


def _dash_error(e: DashError) -> JSONResponse:
    code = {"missing": 404, "refused": 403 if "editores" in str(e) else 400}.get(e.status, 400)
    return JSONResponse({"detail": str(e), "status": e.status}, status_code=code)


class LoadRequest(BaseModel):
    values: dict[str, Any] = {}
    cache: str = "prefer"
    raw: bool = False


class CallRequest(BaseModel):
    tool: str
    args: dict[str, Any] = {}


class SourceEdit(BaseModel):
    args: dict[str, Any]
    values: dict[str, Any] = {}


class Restore(BaseModel):
    version: str


@app.post("/api/dash/{aid}/load/{ds_id}")
async def dash_load(aid: str, ds_id: str, body: LoadRequest, request: Request):
    """La página de un lector pide una fuente: la plataforma media la llamada (nunca credenciales ni SQL libre)."""
    cache = body.cache if body.cache in ("prefer", "auto", "refresh") else "prefer"
    try:
        return await dash.load(aid, ds_id, body.values, cache=cache, raw=body.raw, user=current_user(request))
    except DashError as e:
        return _dash_error(e)


@app.post("/api/dash/{aid}/call/{ds_id}")
async def dash_call(aid: str, ds_id: str, body: CallRequest, request: Request):
    """call() de un loader: otra herramienta del mismo conector (lista blanca)."""
    try:
        return await dash.run_aux(aid, ds_id, body.tool, body.args, user=current_user(request))
    except DashError as e:
        return _dash_error(e)


@app.put("/api/dash/{aid}/runs")
async def dash_runs(aid: str, request: Request):
    body = await request.json()
    try:
        dash.put_runs(aid, current_user(request), body.get("doc") or {})
    except DashError as e:
        return _dash_error(e)
    return {"ok": True}


@app.post("/api/dash/{aid}/datasets/{ds_id}/test")
async def dash_test_source(aid: str, ds_id: str, body: SourceEdit, request: Request):
    try:
        return await dash.test_source(aid, current_user(request), ds_id, body.args, body.values)
    except DashError as e:
        return _dash_error(e)


@app.put("/api/dash/{aid}/datasets/{ds_id}")
async def dash_save_source(aid: str, ds_id: str, body: SourceEdit, request: Request):
    try:
        return dash.save_source(aid, current_user(request), ds_id, body.args)
    except DashError as e:
        return _dash_error(e)


@app.get("/api/dash/{aid}/datasets/{ds_id}/versions")
async def dash_source_versions(aid: str, ds_id: str, request: Request):
    try:
        return dash.source_versions(aid, ds_id, current_user(request))
    except DashError as e:
        return _dash_error(e)


@app.post("/api/dash/{aid}/datasets/{ds_id}/restore")
async def dash_restore_source(aid: str, ds_id: str, body: Restore, request: Request):
    try:
        return dash.restore_source(aid, current_user(request), ds_id, body.version)
    except DashError as e:
        return _dash_error(e)


@app.post("/api/dash/{aid}/datasets/{ds_id}/resume")
async def dash_resume_source(aid: str, ds_id: str, request: Request):
    try:
        return dash.resume_source(aid, current_user(request), ds_id)
    except DashError as e:
        return _dash_error(e)


@app.post("/api/dash/{aid}/assets")
async def dash_upload(aid: str, request: Request, file: UploadFile = File(...)):
    """Adjuntar un archivo desde Fuentes: queda como fuente de tipo archivo."""
    user = current_user(request)
    raw = await file.read()
    name = file.filename or "archivo.csv"
    try:
        dash.require_editor(aid, user)
        asset = dash.store.put_asset(aid, name, raw)
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", name.rsplit(".", 1)[0])[:48].strip("_") or "archivo"
        ds_id, n = base, 2
        while dash.store.get(aid, "datasets", ds_id):
            ds_id, n = f"{base}_{n}", n + 1
        doc = {"title": name.rsplit(".", 1)[0][:40], "description": f"Archivo adjunto {name}.",
               "source": {"kind": "file", **asset}, "updated": {"at": now_iso(), "by": user}}
        result = dash.write(aid, user, [{"op": "set", "collection": "datasets", "doc_id": ds_id, "data": doc}])
    except DashError as e:
        return _dash_error(e)
    return {**result, "dataset": ds_id, **asset}


@app.get("/api/dash/{aid}/_blob/{blob}")
async def dash_asset(aid: str, blob: str, request: Request):
    try:
        dash.require_viewer(aid, current_user(request))
        raw, name = dash.store.get_asset(aid, f"/_blob/{blob}")
    except DashError as e:
        return _dash_error(e)
    return Response(raw, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name or blob}"'})


# ---------------------------------------------------------------- chat


class ClientInfo(BaseModel):
    """Lo que el navegador sabe de la hora de la persona: zona y desfasaje con UTC (minutos)."""

    tz: str | None = None
    utc_offset: int | None = None


class NewMessage(BaseModel):
    message: str
    context: dict[str, Any] | None = None
    client: ClientInfo | None = None
    # Modelo y effort elegidos en el chat; lo que no está en el catálogo vuelve al default.
    model: str | None = None
    effort: str | None = None


@app.get("/api/models")
async def list_models():
    """Catálogo del selector del chat: modelos y el effort que acepta cada uno."""
    return {
        "default": config.AGENT_MODEL,
        "models": [
            {
                "id": c.id,
                "name": c.name,
                "efforts": [{"id": e, "name": model_choice.EFFORT_NAMES[e]} for e in c.efforts],
            }
            for c in model_choice.catalog()
        ],
    }


def _user_state(request: Request, client: ClientInfo | None) -> dict[str, Any]:
    """Nombre de la cuenta y hora local de quien chatea: el agente los lee de session.state."""
    user = request.state.user or {}
    out: dict[str, Any] = {"user_name": user.get("name") or user.get("id", "")}
    if client and client.utc_offset is not None and -14 * 60 <= client.utc_offset <= 14 * 60:
        out["user_utc_offset"] = client.utc_offset
        if client.tz and len(client.tz) <= 64:
            out["user_tz"] = client.tz
    return out


# Bloques que la app antepone al mensaje (hora local y contexto de vista): el chat no los muestra.
_VIEW_CONTEXT = re.compile(r"^(?:<(turn|view)-context>.*?</\1-context>\s*)+", re.S)


def _with_context(text: str, context: dict[str, Any] | None, turn: str = "") -> str:
    """La hora local y el contexto de vista viajan delante del mensaje, como datos (sin valores)."""
    prefix = f"{turn}\n\n" if turn else ""
    if not context:
        return prefix + text
    keep = ("dashboard", "mode", "place", "selected", "at", "field", "filters", "failed", "failures", "flagged", "errors")
    clean = {k: context[k] for k in keep if context.get(k) not in (None, "", [], {})}
    if not clean.get("dashboard"):
        return prefix + text
    return f"{prefix}<view-context>{json.dumps({'context': clean}, ensure_ascii=False)}</view-context>\n\n{text}"


def _strip_context(text: str) -> str:
    return _VIEW_CONTEXT.sub("", text, count=1)


class ChatUpdate(BaseModel):
    title: str | None = None
    archived: bool | None = None


_MAX_TOOL_RESULT_CHARS = 60_000


def _visible_events(events: list) -> list:
    """Eventos vigentes: aplica los rebobinados de ADK (regenerar).

    rewind_async no borra eventos: agrega uno con rewind_before_invocation_id y
    el modelo deja de ver lo anterior. El historial tiene que hacer lo mismo.
    """
    out: list = []
    for e in events:
        target = e.actions.rewind_before_invocation_id if e.actions else None
        if target:
            idx = next((i for i, x in enumerate(out) if x.invocation_id == target), None)
            if idx is not None:
                del out[idx:]
            continue
        out.append(e)
    return out


def _event_items(event) -> list[dict[str, Any]]:
    """Traduce un Event de ADK a los mensajes simples que entiende el front."""
    items: list[dict[str, Any]] = []
    if event.error_message:
        items.append({"type": "error", "message": event.error_message})
    if not event.content or not event.content.parts:
        return items
    for part in event.content.parts:
        # Los eventos parciales (streaming) repiten las llamadas a tools en cada
        # chunk: de ellos solo se toma el texto; las tools llegan en el evento final.
        if event.partial and not part.text:
            continue
        if part.text and not part.thought:
            role = "user" if event.author == "user" else "assistant"
            text = _strip_context(part.text) if role == "user" else part.text
            items.append({"type": "text", "role": role, "text": text, "partial": bool(event.partial), "model": event.model_version})
        elif part.function_call:
            fc = part.function_call
            items.append({"type": "tool_call", "id": fc.id, "name": fc.name, "args": fc.args or {}, "model": event.model_version})
        elif part.function_response:
            fr = part.function_response
            resp = fr.response or {}
            ok = "error" not in resp and resp.get("ok", True) is not False
            raw = json.dumps(resp, ensure_ascii=False, default=str)
            # Resultado completo para las tool UIs; si es enorme, recortado como texto.
            result = resp if len(raw) <= _MAX_TOOL_RESULT_CHARS else raw[:_MAX_TOOL_RESULT_CHARS] + "…"
            items.append({"type": "tool_result", "id": fr.id, "name": fr.name, "result": result, "ok": ok})
            opens = (fr.name or "").startswith("artifact_") or fr.name in ("create_dashboard", "write_db")
            if opens and isinstance(resp, dict) and resp.get("artifact_id") and resp.get("ok", True) is not False:
                items.append({"type": "artifact", "artifact_id": resp["artifact_id"], "tool": fr.name})
    return items


@app.get("/api/chats")
async def list_chats(request: Request):
    res = await session_service.list_sessions(app_name=config.APP_NAME, user_id=current_user(request))
    running = runs.running_ids()
    chats = [
        {
            "id": s.id,
            "title": s.state.get("chat_title") or "Chat nuevo",
            "archived": bool(s.state.get("chat_archived")),
            "running": s.id in running,
            "updated": s.last_update_time,
        }
        for s in res.sessions
    ]
    return sorted(chats, key=lambda c: c["updated"], reverse=True)


@app.post("/api/chats")
async def create_chat(request: Request):
    s = await session_service.create_session(app_name=config.APP_NAME, user_id=current_user(request))
    return {"id": s.id, "title": "Chat nuevo"}


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str, request: Request):
    s = await session_service.get_session(app_name=config.APP_NAME, user_id=current_user(request), session_id=chat_id)
    if not s:
        raise HTTPException(404, "No existe el chat.")
    run = runs.active(chat_id)
    # Con un turno en curso, el historial llega hasta antes de ese turno: el turno
    # completo (desde el mensaje del usuario) se repite por /stream.
    events = [e for e in _visible_events(s.events) if not e.partial and not (run and e.timestamp >= run.started_at - 1)]
    items = [i for e in events for i in _event_items(e)]
    return {
        "id": s.id,
        "title": s.state.get("chat_title") or "Chat nuevo",
        "items": items,
        "running": run is not None,
    }


@app.patch("/api/chats/{chat_id}")
async def update_chat(chat_id: str, body: ChatUpdate, request: Request):
    """Renombrar o archivar: se guarda en el estado de la sesión con un evento sin contenido."""
    s = await session_service.get_session(app_name=config.APP_NAME, user_id=current_user(request), session_id=chat_id)
    if not s:
        raise HTTPException(404, "No existe el chat.")
    delta: dict[str, Any] = {}
    if body.title is not None and body.title.strip():
        delta["chat_title"] = body.title.strip()[:120]
    if body.archived is not None:
        delta["chat_archived"] = body.archived
    if delta:
        await session_service.append_event(
            s, Event(author="app", invocation_id=f"ui-{secrets.token_hex(6)}", actions=EventActions(state_delta=delta))
        )
    return {"ok": True}


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, request: Request):
    await _require_chat(chat_id, current_user(request))
    runs.cancel(chat_id)
    await session_service.delete_session(app_name=config.APP_NAME, user_id=current_user(request), session_id=chat_id)
    return {"ok": True}


def _sse(run: ChatRun) -> StreamingResponse:
    """Repite el turno desde el principio y sigue en vivo. Cortar la conexión no lo detiene."""

    return StreamingResponse(
        sse_events(run),
        media_type="text/event-stream",
        # Sin buffering en proxies (nginx respeta este header; Cloudflare no bufferiza text/event-stream).
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chats/{chat_id}/run")
async def run_chat(chat_id: str, body: NewMessage, request: Request):
    user = current_user(request)
    s = await session_service.get_session(app_name=config.APP_NAME, user_id=user, session_id=chat_id)
    if not s:
        raise HTTPException(404, "No existe el chat.")
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Mensaje vacío.")
    if runs.active(chat_id):
        raise HTTPException(409, "Ya hay un turno en curso en este chat.")
    state_delta = _user_state(request, body.client)
    model, effort = model_choice.resolve(body.model, body.effort)
    state_delta[model_choice.MODEL_KEY] = model
    state_delta[model_choice.EFFORT_KEY] = effort
    if not s.state.get("chat_title"):
        state_delta["chat_title"] = text[:70]
    turn = turn_context({**s.state, **state_delta})
    return _sse(_start_turn(user, chat_id, _with_context(text, body.context, turn), state_delta))


def _start_turn(user: str, chat_id: str, text: str, state_delta: dict[str, Any] | None = None) -> ChatRun:
    """`text` es lo que recibe el modelo (con el contexto de vista, si hay); el chat muestra solo las palabras."""
    async def produce(run: ChatRun) -> None:
        async for event in runner.run_async(
            user_id=user,
            session_id=chat_id,
            new_message=types.Content(role="user", parts=[types.Part(text=text)]),
            state_delta=state_delta,
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
        ):
            for item in _event_items(event):
                await run.push(item)

    first = [{"type": "text", "role": "user", "text": _strip_context(text), "partial": False}]
    return runs.start(chat_id, first, produce)


@app.post("/api/chats/{chat_id}/regenerate")
async def regenerate_chat(chat_id: str, request: Request):
    """Rebobina la sesión a antes del último mensaje del usuario y vuelve a correr ese turno."""
    user = current_user(request)
    s = await session_service.get_session(app_name=config.APP_NAME, user_id=user, session_id=chat_id)
    if not s:
        raise HTTPException(404, "No existe el chat.")
    if runs.active(chat_id):
        raise HTTPException(409, "Ya hay un turno en curso en este chat.")
    last_user = next(
        (e for e in reversed(_visible_events(s.events)) if e.author == "user" and e.content and any(p.text for p in e.content.parts or [])),
        None,
    )
    if not last_user:
        raise HTTPException(400, "No hay un mensaje para regenerar.")
    text = "".join(p.text or "" for p in last_user.content.parts)
    await runner.rewind_async(user_id=user, session_id=chat_id, rewind_before_invocation_id=last_user.invocation_id)
    return _sse(_start_turn(user, chat_id, text))


class Feedback(BaseModel):
    type: str
    text: str = ""
    comment: str = ""


@app.post("/api/chats/{chat_id}/feedback")
async def chat_feedback(chat_id: str, body: Feedback, request: Request):
    """Útil / no útil sobre una respuesta: queda en data/feedback.jsonl para revisar."""
    record = {
        "at": time.time(),
        "user": current_user(request),
        "chat_id": chat_id,
        "type": body.type,
        "comment": body.comment[:2000],
        "text": body.text[:2000],
    }
    with (config.DATA_DIR / "feedback.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True}


@app.get("/api/suggestions")
async def get_suggestions(request: Request, chat_id: str | None = None):
    """Próximas consultas sugeridas: de inicio (sin chat) o de seguimiento (con chat)."""
    user = current_user(request)
    if chat_id:
        s = await session_service.get_session(app_name=config.APP_NAME, user_id=user, session_id=chat_id)
        if not s or runs.active(chat_id):
            return []
        turns = [
            {"de": "usuario" if e.author == "user" else "asistente", "texto": _strip_context("".join(p.text or "" for p in e.content.parts))[:1500]}
            for e in _visible_events(s.events)
            if not e.partial and e.content and e.content.parts and any(p.text and not p.thought for p in e.content.parts)
        ][-4:]
        if not turns:
            return []
        key = f"chat:{chat_id}:{len(s.events)}"
        return await suggestions.generate(key, {"ultimos_mensajes": turns}, follow_up=True)

    res = await session_service.list_sessions(app_name=config.APP_NAME, user_id=user)
    recent = sorted(res.sessions, key=lambda x: x.last_update_time, reverse=True)[:10]
    arts = (await list_artifacts(request))[:8]
    context = {
        "chats_recientes": [x.state.get("chat_title") for x in recent if x.state.get("chat_title")],
        "artifacts_recientes": [{"titulo": a["title"], "tipo": a["kind"], "descripcion": a.get("description", "")} for a in arts],
    }
    key = "home:" + str(recent[0].last_update_time if recent else 0) + ":" + str(len(arts))
    return await suggestions.generate(key, context, follow_up=False)


async def _require_chat(chat_id: str, user: str) -> None:
    s = await session_service.get_session(app_name=config.APP_NAME, user_id=user, session_id=chat_id)
    if not s:
        raise HTTPException(404, "No existe el chat.")


@app.get("/api/chats/{chat_id}/stream")
async def follow_chat(chat_id: str, request: Request):
    """Engancharse al turno en curso (o recién terminado) de un chat."""
    await _require_chat(chat_id, current_user(request))
    run = runs.get(chat_id)
    if not run:
        raise HTTPException(404, "No hay un turno en curso en este chat.")
    return _sse(run)


@app.post("/api/chats/{chat_id}/cancel")
async def cancel_chat(chat_id: str, request: Request):
    await _require_chat(chat_id, current_user(request))
    return {"ok": runs.cancel(chat_id)}
