"""Login con Google, sesión, proyectos, chats compartidos y administración de usuarios.

Todo lo que no es público pasa por `require_login` (middleware): sin sesión, la API
responde 401 y las páginas redirigen a /login. Los pedidos que cambian algo además
tienen que venir del mismo origen (defensa contra CSRF, junto con SameSite=Lax).
"""

from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .. import config
from ..artifacts.dashboard import DashboardService, DashError
from ..auth import crypto, google
from ..auth.access import Access, AccessError
from ..auth.store import USER_ROLES, AppStore, clean_email
from ..warehouse import credentials

logger = logging.getLogger("agent_dashboards.auth")

SESSION_COOKIE = "ad_session"
FLOW_COOKIE = "ad_oauth"
_PUBLIC = ("/auth/", "/static/", "/healthz", "/login", "/favicon.ico")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

ERRORS = {
    "config": "El login con Google no está configurado: faltan GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET.",
    "denied": "Esta cuenta no tiene acceso. Pedile a un admin que te invite.",
    "cancelled": "Cancelaste el login.",
    "expired": "El login venció. Probá de nuevo.",
    "consent": "Para consultar BigQuery con tu cuenta hace falta que aceptes el permiso de BigQuery.",
}


# --- utilidades --------------------------------------------------------------------------


def _public_base(request: Request) -> str:
    if config.PUBLIC_URL:
        return config.PUBLIC_URL
    return f"{request.url.scheme}://{request.url.netloc}"


def _secure(request: Request) -> bool:
    return _public_base(request).startswith("https://")


def _same_origin(origin: str, request: Request) -> bool:
    want = urlsplit(_public_base(request))
    got = urlsplit(origin)
    return (got.scheme, got.netloc) == (want.scheme, want.netloc)


def _safe_next(target: str | None) -> str:
    """Solo rutas internas: evita redirecciones abiertas (//otro.sitio, https://…)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def _err(e: Exception) -> JSONResponse:
    code = {"missing": 404, "forbidden": 403, "invalid": 400}.get(getattr(e, "code", ""), 400)
    return JSONResponse({"detail": str(e)}, status_code=code)


def current_user(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if not user:  # el middleware ya lo frena; esto es por si una ruta pública lo llama
        from fastapi import HTTPException

        raise HTTPException(401, "Iniciá sesión.")
    return user["id"]


# --- página de login ------------------------------------------------------------------------

_LOGIN_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ingresar · Open BI Artifacts</title>
<style>
:root {{ color-scheme: light dark; --bg:#fafafa; --fg:#18181b; --muted:#71717a; --card:#fff; --border:#e4e4e7; --err:#b91c1c; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#09090b; --fg:#fafafa; --muted:#a1a1aa; --card:#18181b; --border:#27272a; --err:#f87171; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; min-height:100dvh; display:grid; place-items:center; background:var(--bg); color:var(--fg);
       font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; padding:16px; }}
main {{ width:100%; max-width:360px; background:var(--card); border:1px solid var(--border); border-radius:16px; padding:28px; }}
h1 {{ font-size:18px; margin:0 0 4px; }}
p {{ margin:0 0 20px; color:var(--muted); }}
a.btn {{ display:flex; align-items:center; justify-content:center; gap:10px; padding:10px 14px; border-radius:10px;
        border:1px solid var(--border); color:var(--fg); text-decoration:none; font-weight:500; }}
a.btn:hover {{ background:color-mix(in srgb, var(--fg) 6%, transparent); }}
.err {{ color:var(--err); margin:0 0 16px; }}
</style></head>
<body><main>
<h1>Open BI Artifacts</h1>
<p>Ingresá con tu cuenta de Google.</p>
{error}
{button}
</main></body></html>"""

_GOOGLE_ICON = (
    '<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">'
    '<path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.6-.4-3.5z"/>'
    '<path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>'
    '<path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/>'
    '<path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.6-.4-3.5z"/></svg>'
)


def login_page(error: str | None, next_path: str) -> HTMLResponse:
    msg = ERRORS.get(error or "", "No se pudo completar el login. Probá de nuevo." if error else "")
    if not google.configured():
        msg = ERRORS["config"]
    button = (
        f'<a class="btn" href="/auth/google?next={quote(next_path)}">{_GOOGLE_ICON}Ingresar con Google</a>'
        if google.configured() else ""
    )
    page = _LOGIN_HTML.format(error=f'<p class="err" role="alert">{html.escape(msg)}</p>' if msg else "", button=button)
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


# --- modelos de los pedidos -------------------------------------------------------------------


class ProjectIn(BaseModel):
    name: str


class MemberIn(BaseModel):
    email: str
    role: str = "viewer"


class RoleIn(BaseModel):
    role: str


class MoveIn(BaseModel):
    project_id: str | None = None


class ShareIn(BaseModel):
    project_id: str


class InviteIn(BaseModel):
    email: str
    role: str = "member"


class UserPatch(BaseModel):
    role: str | None = None
    status: str | None = None


# --- armado -------------------------------------------------------------------------------------


class AuthContext:
    def __init__(self, store: AppStore):
        self.store = store
        self.access = Access(store)
        self.broker = google.TokenBroker(store)


def install(
    app: FastAPI,
    ctx: AuthContext,
    *,
    dash: DashboardService,
    chat_snapshot: Callable[[str, str], Awaitable[tuple[str, list[dict[str, Any]]] | None]],
    is_curator: Callable[[str], bool],
) -> None:
    """Middleware de sesión y rutas de login, cuenta, proyectos, compartidos y admin."""

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if request.method not in _SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and not _same_origin(origin, request):
                return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
        request.state.user = ctx.store.session_user(request.cookies.get(SESSION_COOKIE, ""))
        if path.startswith(_PUBLIC) or request.state.user:
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Iniciá sesión.", "login": "/login"}, status_code=401)
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target)}", status_code=303)

    r = APIRouter()

    # --- login -------------------------------------------------------------------------

    @r.get("/login", include_in_schema=False)
    async def login(request: Request, error: str | None = None, next: str = "/"):  # noqa: A002
        if request.state.user:
            return RedirectResponse(_safe_next(next), status_code=303)
        return login_page(error, _safe_next(next))

    @r.get("/auth/google", include_in_schema=False)
    async def auth_google(request: Request, next: str = "/", consent: int = 0, hint: str | None = None):  # noqa: A002
        if not google.configured():
            return RedirectResponse("/login?error=config", status_code=303)
        flow = google.new_flow()
        redirect_uri = f"{_public_base(request)}/auth/callback"
        url = google.authorization_url(redirect_uri, flow, consent=bool(consent), login_hint=hint)
        resp = RedirectResponse(url, status_code=303)
        cookie = crypto.sign({"state": flow["state"], "nonce": flow["nonce"], "verifier": flow["verifier"],
                              "next": _safe_next(next), "consent": int(consent)}, max_age=600)
        resp.set_cookie(FLOW_COOKIE, cookie, max_age=600, httponly=True, secure=_secure(request), samesite="lax", path="/auth")
        return resp

    @r.get("/auth/callback", include_in_schema=False)
    async def auth_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
        flow = crypto.unsign(request.cookies.get(FLOW_COOKIE, ""))

        def fail(reason: str) -> RedirectResponse:
            resp = RedirectResponse(f"/login?error={reason}&next={quote((flow or {}).get('next', '/'))}", status_code=303)
            resp.delete_cookie(FLOW_COOKIE, path="/auth")
            return resp

        if error:
            return fail("cancelled" if error == "access_denied" else "google")
        if not flow or not code or not state or state != flow.get("state"):
            return fail("expired")
        redirect_uri = f"{_public_base(request)}/auth/callback"
        try:
            tokens = await google.exchange_code(code, redirect_uri, flow["verifier"])
            claims = await google.verify_id_token(tokens.get("id_token", ""), flow["nonce"])
        except google.LoginError as e:
            logger.warning("Login fallido: %s", e)
            return fail("google")
        email = clean_email(claims["email"])
        if not ctx.access.may_login(email, claims.get("hd")):
            logger.info("Login rechazado para %s", email)
            return fail("denied")
        existing = ctx.store.user(email)
        if existing and existing.get("google_sub") and existing["google_sub"] != claims["sub"]:
            logger.warning("La cuenta %s cambió de sub de Google: se rechaza", email)
            return fail("denied")
        role = "admin" if email in config.ADMIN_EMAILS else (existing or {}).get("role", "member")
        ctx.store.record_login(email, claims["sub"], claims.get("name"), claims.get("picture"), role)
        ctx.broker.save(email, tokens)
        if google.reader_mode() and not ctx.broker.has_bigquery(email):
            if flow.get("consent") or (tokens.get("scope") and not google.granted_bigquery(tokens)):
                return fail("consent")  # ya se pidió el consentimiento, o no aceptó el permiso de BigQuery
            # Google no mandó el refresh token (ya había consentido antes): se pide de nuevo, explícito.
            resp = RedirectResponse(f"/auth/google?consent=1&hint={quote(email)}&next={quote(flow['next'])}", status_code=303)
            resp.delete_cookie(FLOW_COOKIE, path="/auth")
            return resp
        token = ctx.store.create_session(email, config.SESSION_DAYS, request.headers.get("user-agent"))
        resp = RedirectResponse(flow["next"], status_code=303)
        resp.delete_cookie(FLOW_COOKIE, path="/auth")
        resp.set_cookie(SESSION_COOKIE, token, max_age=int(config.SESSION_DAYS * 86400), httponly=True,
                        secure=_secure(request), samesite="lax", path="/")
        return resp

    @r.post("/auth/logout", include_in_schema=False)
    async def logout(request: Request):
        token = request.cookies.get(SESSION_COOKIE, "")
        if request.state.user:
            ctx.broker.forget(request.state.user["id"])
        if token:
            ctx.store.delete_session(token)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    # --- cuenta ---------------------------------------------------------------------------

    @r.get("/api/me")
    async def me(request: Request):
        user = request.state.user
        uid = user["id"]
        return {
            "id": uid,
            "name": user.get("name") or uid,
            "picture": user.get("picture"),
            "admin": ctx.access.is_admin(uid),
            "curator": is_curator(uid),
            "projects": ctx.store.user_projects(uid),
            # Lo efectivo: sin login de Google configurado se consulta con la cuenta del servidor.
            "query_credentials": "reader" if credentials.per_user() else "server",
            "bigquery_ok": (not credentials.per_user()) or ctx.broker.has_bigquery(uid),
        }

    # --- proyectos -----------------------------------------------------------------------------

    @r.get("/api/projects")
    async def projects(request: Request):
        return ctx.store.user_projects(current_user(request))

    @r.post("/api/projects")
    async def create_project(body: ProjectIn, request: Request):
        name = body.name.strip()[:80]
        if not name:
            return JSONResponse({"detail": "El proyecto necesita un nombre."}, status_code=400)
        return {**ctx.store.create_project(name, current_user(request)), "role": "owner"}

    @r.get("/api/projects/{pid}")
    async def project(pid: str, request: Request):
        try:
            p = ctx.access.require_project(pid, current_user(request))
        except AccessError as e:
            return _err(e)
        return {**p, "members": ctx.store.members(pid), "shares": ctx.store.project_shares(pid)}

    @r.patch("/api/projects/{pid}")
    async def rename_project(pid: str, body: ProjectIn, request: Request):
        try:
            ctx.access.require_project(pid, current_user(request), "owner")
        except AccessError as e:
            return _err(e)
        name = body.name.strip()[:80]
        if not name:
            return JSONResponse({"detail": "El proyecto necesita un nombre."}, status_code=400)
        ctx.store.rename_project(pid, name)
        return {"ok": True}

    @r.delete("/api/projects/{pid}")
    async def archive_project(pid: str, request: Request):
        """Archiva el proyecto: deja de verse; sus dashboards vuelven a ser personales de quien los creó."""
        try:
            ctx.access.require_project(pid, current_user(request), "owner")
        except AccessError as e:
            return _err(e)
        for d in dash.store.list():
            if d.get("project_id") == pid:
                dash.store.set_project(d["id"], None)
        ctx.store.archive_project(pid)
        return {"ok": True}

    @r.post("/api/projects/{pid}/members")
    async def add_member(pid: str, body: MemberIn, request: Request):
        try:
            ctx.access.add_member(pid, current_user(request), body.email, body.role)
        except AccessError as e:
            return _err(e)
        return {"ok": True, "members": ctx.store.members(pid)}

    @r.patch("/api/projects/{pid}/members/{email}")
    async def change_member(pid: str, email: str, body: RoleIn, request: Request):
        try:
            ctx.access.change_member(pid, current_user(request), email, body.role)
        except AccessError as e:
            return _err(e)
        return {"ok": True, "members": ctx.store.members(pid)}

    @r.delete("/api/projects/{pid}/members/{email}")
    async def remove_member(pid: str, email: str, request: Request):
        try:
            ctx.access.change_member(pid, current_user(request), email, None)
        except AccessError as e:
            return _err(e)
        return {"ok": True, "members": ctx.store.members(pid)}

    # --- dashboards en proyectos -----------------------------------------------------------------

    @r.post("/api/artifacts/{aid}/project")
    async def move_artifact(aid: str, body: MoveIn, request: Request):
        user = current_user(request)
        try:
            platform = dash.require_viewer(aid, user)
        except DashError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        if not ctx.access.can_move(platform, user, body.project_id):
            return JSONResponse({"detail": "No podés mover este dashboard ahí."}, status_code=403)
        dash.store.set_project(aid, body.project_id)
        return {"ok": True, "project_id": body.project_id}

    # --- chats compartidos -------------------------------------------------------------------------

    @r.post("/api/chats/{chat_id}/share")
    async def share_chat(chat_id: str, body: ShareIn, request: Request):
        """Copia de solo lectura del chat para los miembros de un proyecto (el chat sigue siendo tuyo)."""
        user = current_user(request)
        try:
            ctx.access.require_project(body.project_id, user, "editor")
        except AccessError as e:
            return _err(e)
        snap = await chat_snapshot(user, chat_id)
        if not snap:
            return JSONResponse({"detail": "No existe el chat."}, status_code=404)
        title, items = snap
        share = ctx.store.create_share(chat_id, user, body.project_id, title, items)
        return {k: share[k] for k in ("id", "project_id", "title", "created_at")}

    @r.get("/api/shares/{sid}")
    async def get_share(sid: str, request: Request):
        share = ctx.store.share(sid)
        if not share or ctx.access.project_role(share["project_id"], current_user(request)) is None:
            return JSONResponse({"detail": "No existe el chat compartido."}, status_code=404)
        return share

    @r.delete("/api/shares/{sid}")
    async def delete_share(sid: str, request: Request):
        user = current_user(request)
        share = ctx.store.share(sid)
        if not share or ctx.access.project_role(share["project_id"], user) is None:
            return JSONResponse({"detail": "No existe el chat compartido."}, status_code=404)
        if share["owner"] != user and ctx.access.project_role(share["project_id"], user) != "owner":
            return JSONResponse({"detail": "Lo borra quien lo compartió o un owner del proyecto."}, status_code=403)
        ctx.store.delete_share(sid)
        return {"ok": True}

    # --- admin ----------------------------------------------------------------------------------

    def _admin(request: Request) -> str | None:
        user = current_user(request)
        return user if ctx.access.is_admin(user) else None

    @r.get("/api/admin/users")
    async def users(request: Request):
        if not _admin(request):
            return JSONResponse({"detail": "Solo admins."}, status_code=403)
        return [{**u, "admin": ctx.access.is_admin(u["id"])} for u in ctx.store.users()]

    @r.post("/api/admin/users")
    async def invite(body: InviteIn, request: Request):
        actor = _admin(request)
        if not actor:
            return JSONResponse({"detail": "Solo admins."}, status_code=403)
        email = clean_email(body.email)
        if "@" not in email or body.role not in USER_ROLES:
            return JSONResponse({"detail": "Mail o rol inválido."}, status_code=400)
        ctx.store.add_user(email, role=body.role, invited_by=actor)
        return ctx.store.update_user(email, role=body.role, status="active")

    @r.patch("/api/admin/users/{email}")
    async def update_user(email: str, body: UserPatch, request: Request):
        actor = _admin(request)
        if not actor:
            return JSONResponse({"detail": "Solo admins."}, status_code=403)
        email = clean_email(email)
        if not ctx.store.user(email):
            return JSONResponse({"detail": "No existe ese usuario."}, status_code=404)
        if email == actor and (body.status == "disabled" or body.role == "member"):
            return JSONResponse({"detail": "No podés sacarte el acceso de admin ni deshabilitarte."}, status_code=400)
        if (body.role and body.role not in USER_ROLES) or (body.status and body.status not in ("active", "disabled")):
            return JSONResponse({"detail": "Rol o estado inválido."}, status_code=400)
        user = ctx.store.update_user(email, role=body.role, status=body.status)
        if body.status == "disabled":
            ctx.store.delete_user_sessions(email)
            ctx.broker.forget(email)
        return user

    app.include_router(r)
