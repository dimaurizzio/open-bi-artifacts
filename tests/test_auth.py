"""Login con Google, sesiones, proyectos, permisos, credenciales por persona y limpieza."""

import asyncio
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_dashboards import config
from agent_dashboards.artifacts.dashboard import DashboardService, DashError, DashStore
from agent_dashboards.auth import crypto, google
from agent_dashboards.auth.access import Access, AccessError
from agent_dashboards.auth.store import AppStore
from agent_dashboards.server import auth_api, retention
from agent_dashboards.warehouse import credentials
from agent_dashboards.warehouse.mcp_client import QueryResult

BASE = "http://testserver"


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SECRET_KEY", "clave-de-prueba")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "cliente")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "secreto")
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    monkeypatch.setattr(config, "ALLOWED_EMAIL_DOMAINS", {"empresa.com"})
    monkeypatch.setattr(config, "ALLOWED_EMAILS", {"externo@gmail.com"})
    monkeypatch.setattr(config, "ADMIN_EMAILS", {"jefa@empresa.com"})
    monkeypatch.setattr(config, "QUERY_CREDENTIALS", "reader")
    monkeypatch.setattr(config, "WAREHOUSE_DATASETS", ["proj.ds"])
    crypto.reset_for_tests()
    yield
    crypto.reset_for_tests()
    credentials.set_token_provider(None)


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "app.db")


@pytest.fixture
def access(store):
    return Access(store)


# --- firma y cifrado ------------------------------------------------------------------


def test_signed_values_detect_tampering_and_expiry():
    value = crypto.sign({"state": "s1"}, max_age=60)
    assert crypto.unsign(value) == {"state": "s1"}
    body, mac = value.split(".")
    assert crypto.unsign(body + "x." + mac) is None
    assert crypto.unsign(crypto.sign({"state": "s1"}, max_age=-1)) is None
    secret = crypto.encrypt("1//refresh")
    assert secret != "1//refresh" and crypto.decrypt(secret) == "1//refresh"
    assert crypto.decrypt("basura") is None


# --- quién entra ---------------------------------------------------------------------------


def test_who_may_login(access, store):
    assert access.may_login("ana@empresa.com", "empresa.com")
    assert not access.may_login("ana@empresa.com", None)  # sin hd: no es una cuenta del Workspace
    assert access.may_login("externo@gmail.com")
    assert access.may_login("JEFA@empresa.com")
    assert not access.may_login("nadie@gmail.com")
    store.add_user("invitado@gmail.com", invited_by="jefa@empresa.com")
    assert access.may_login("invitado@gmail.com")
    store.update_user("invitado@gmail.com", status="disabled")
    assert not access.may_login("invitado@gmail.com")


def test_sessions_expire_and_follow_user_status(store):
    store.add_user("ana@empresa.com")
    token = store.create_session("ana@empresa.com", days=1)
    assert store.session_user(token)["id"] == "ana@empresa.com"
    assert store.session_user("otro") is None
    store.update_user("ana@empresa.com", status="disabled")
    assert store.session_user(token) is None
    store.update_user("ana@empresa.com", status="active")
    old = store.create_session("ana@empresa.com", days=-1)
    assert store.session_user(old) is None
    assert store.purge_expired_sessions() == 1


# --- proyectos y permisos ------------------------------------------------------------------------


def test_project_roles_and_members(store, access):
    p = store.create_project("Ventas", "ana@empresa.com")
    access.add_member(p["id"], "ana@empresa.com", "beto@empresa.com", "editor")
    access.add_member(p["id"], "ana@empresa.com", "carla@empresa.com", "viewer")
    with pytest.raises(AccessError):  # un editor no administra miembros
        access.add_member(p["id"], "beto@empresa.com", "dani@empresa.com", "viewer")
    with pytest.raises(AccessError):  # un owner no puede abrir el acceso a cualquier mail
        access.add_member(p["id"], "ana@empresa.com", "nadie@gmail.com", "viewer")
    store.add_user("jefa@empresa.com", role="admin")
    store.set_member(p["id"], "jefa@empresa.com", "owner", "ana@empresa.com")
    access.add_member(p["id"], "jefa@empresa.com", "nadie@gmail.com", "viewer")  # un admin sí: queda invitado
    assert access.may_login("nadie@gmail.com")
    with pytest.raises(AccessError):  # no se cambia el rol de alguien que no es miembro
        access.change_member(p["id"], "ana@empresa.com", "x@empresa.com", "editor")
    store.remove_member(p["id"], "jefa@empresa.com")
    with pytest.raises(AccessError, match="sin owner"):
        access.change_member(p["id"], "ana@empresa.com", "ana@empresa.com", "editor")
    access.change_member(p["id"], "carla@empresa.com", "carla@empresa.com", None)  # cualquiera se va solo
    assert {m["user_id"] for m in store.members(p["id"])} == {"ana@empresa.com", "beto@empresa.com", "nadie@gmail.com"}


def test_artifact_roles_follow_the_project(store, access):
    p = store.create_project("Ventas", "ana@empresa.com")
    store.set_member(p["id"], "beto@empresa.com", "editor", "ana@empresa.com")
    store.set_member(p["id"], "carla@empresa.com", "viewer", "ana@empresa.com")
    personal = {"owner": "ana@empresa.com"}
    shared = {"owner": "ana@empresa.com", "project_id": p["id"]}
    assert access.can_edit(personal, "ana@empresa.com") and not access.can_view(personal, "beto@empresa.com")
    assert access.can_edit(shared, "beto@empresa.com")
    assert access.can_view(shared, "carla@empresa.com") and not access.can_edit(shared, "carla@empresa.com")
    assert not access.can_view(shared, "dani@empresa.com")
    assert access.can_move(personal, "ana@empresa.com", p["id"])
    assert not access.can_move(shared, "carla@empresa.com", None)
    assert not access.can_move(personal, "ana@empresa.com", "p_otro")
    store.archive_project(p["id"])
    assert not access.can_view(shared, "carla@empresa.com")


# --- dashboards con permisos y credenciales por persona ----------------------------------------------


class FakeWarehouse:
    def __init__(self, who):
        self.who = who
        self.calls: list[str] = []

    async def dry_run(self, sql):
        return 10

    async def run_query(self, sql, **_):
        self.calls.append(self.who)
        return QueryResult(columns=[{"name": "n", "type": "INTEGER"}], rows=[{"n": 1}], total_rows=1)

    async def call_tool(self, name, args):
        return {}


def test_dashboard_runs_with_reader_credentials_and_cache_is_per_person(tmp_path, store, access):
    svc = DashboardService(DashStore(tmp_path / "arts"))
    svc.can_view, svc.can_edit = access.can_view, access.can_edit
    clients: dict[str, FakeWarehouse] = {}
    svc.client_for = lambda user: clients.setdefault(user, FakeWarehouse(user))
    svc.per_user_cache = lambda: True
    aid = svc.store.create("Ventas", "ana@empresa.com")
    source = {"kind": "query", "connector": "Google Cloud BigQuery", "tool": "execute_sql_readonly",
              "args": {"query": "SELECT 1 AS n FROM `proj.ds.t`"}}
    svc.write(aid, "ana@empresa.com", [{"op": "set", "collection": "datasets", "doc_id": "d",
                                        "data": {"title": "N", "description": "Lee proj.ds.t.", "source": source}}])
    asyncio.run(svc.load(aid, "d", {}, user="ana@empresa.com"))
    with pytest.raises(DashError, match="No existe"):
        asyncio.run(svc.load(aid, "d", {}, user="beto@empresa.com"))
    p = store.create_project("Ventas", "ana@empresa.com")
    store.set_member(p["id"], "beto@empresa.com", "viewer", "ana@empresa.com")
    svc.store.set_project(aid, p["id"])
    asyncio.run(svc.load(aid, "d", {}, user="beto@empresa.com"))
    # Beto no recibió la caché de Ana: corrió con sus credenciales.
    assert clients["ana@empresa.com"].calls == ["ana@empresa.com"]
    assert clients["beto@empresa.com"].calls == ["beto@empresa.com"]
    with pytest.raises(DashError, match="editores"):
        svc.write(aid, "beto@empresa.com", [{"op": "set", "collection": "dash", "doc_id": "meta", "data": {"title": "x"}}])


def test_client_for_uses_server_credentials_without_provider():
    assert not credentials.per_user()
    assert credentials.client_for("ana@empresa.com") is credentials.server_client

    async def provider(user):
        return f"token-de-{user}"

    credentials.set_token_provider(provider)
    client = credentials.client_for("ana@empresa.com")
    assert client is not credentials.server_client
    assert asyncio.run(client.headers()) == {"Authorization": "Bearer token-de-ana@empresa.com"}


def test_token_broker_refreshes_and_drops_revoked_tokens(store, monkeypatch):
    broker = google.TokenBroker(store)
    store.add_user("ana@empresa.com")
    with pytest.raises(google.NeedsGoogleConsent):
        asyncio.run(broker.access_token("ana@empresa.com"))
    broker.save("ana@empresa.com", {"refresh_token": "1//r", "scope": f"openid {config.GOOGLE_BIGQUERY_SCOPE}"})
    assert broker.has_bigquery("ana@empresa.com")
    assert store.google_token("ana@empresa.com")["refresh_token"] != "1//r"  # guardado cifrado
    seen = []

    async def ok(data):
        seen.append(data)
        return {"access_token": "ya29.x", "expires_in": 3600}

    monkeypatch.setattr(google, "_post_token", ok)
    assert asyncio.run(broker.access_token("ana@empresa.com")) == "ya29.x"
    assert asyncio.run(broker.access_token("ana@empresa.com")) == "ya29.x"
    assert len(seen) == 1 and seen[0]["refresh_token"] == "1//r"  # el segundo vino de memoria

    async def revoked(data):
        raise google.LoginError("x", "token endpoint 400: {'error': 'invalid_grant'}")

    broker.forget("ana@empresa.com")
    monkeypatch.setattr(google, "_post_token", revoked)
    with pytest.raises(google.NeedsGoogleConsent):
        asyncio.run(broker.access_token("ana@empresa.com"))
    assert store.google_token("ana@empresa.com") is None


# --- flujo completo por HTTP ------------------------------------------------------------------------


@pytest.fixture
def app_client(tmp_path, store, monkeypatch):
    ctx = auth_api.AuthContext(store)
    dash = DashboardService(DashStore(tmp_path / "arts"))
    dash.can_view, dash.can_edit = ctx.access.can_view, ctx.access.can_edit
    app = FastAPI()

    async def snapshot(user, chat_id):
        return ("Mi chat", [{"type": "text", "role": "user", "text": "hola"}]) if chat_id == "c1" else None

    auth_api.install(app, ctx, dash=dash, chat_snapshot=snapshot, is_curator=lambda u: False)

    @app.get("/")
    async def home(request: Request):
        return {"home": auth_api.current_user(request)}

    @app.post("/api/algo")
    async def algo(request: Request):
        return {"ok": auth_api.current_user(request)}

    claims = {"email": "ana@empresa.com", "email_verified": True, "hd": "empresa.com", "sub": "111", "name": "Ana"}

    async def exchange(code, redirect_uri, verifier):
        assert redirect_uri == f"{BASE}/auth/callback" and verifier
        return {"id_token": "jwt", "access_token": "ya29", "expires_in": 3600,
                "scope": f"openid email profile {config.GOOGLE_BIGQUERY_SCOPE}",
                **({"refresh_token": "1//r"} if code == "con-refresh" else {})}

    async def verify(token, nonce):
        return {**claims, "nonce": nonce}

    monkeypatch.setattr(google, "exchange_code", exchange)
    monkeypatch.setattr(google, "verify_id_token", verify)
    client = TestClient(app, base_url=BASE)
    client.claims = claims
    client.ctx = ctx
    client.dash = dash
    return client


def _login(client, code="con-refresh", next_path="/"):
    r = client.get("/auth/google", params={"next": next_path}, follow_redirects=False)
    assert r.status_code == 303
    query = parse_qs(urlsplit(r.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]
    assert config.GOOGLE_BIGQUERY_SCOPE in query["scope"][0]
    return client.get("/auth/callback", params={"code": code, "state": query["state"][0]}, follow_redirects=False)


def test_without_session_api_is_401_and_pages_redirect(app_client):
    assert app_client.get("/api/me").status_code == 401
    r = app_client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/"
    page = app_client.get("/login")
    assert page.status_code == 200 and "Ingresar con Google" in page.text


def test_login_flow_creates_a_session(app_client):
    r = _login(app_client, next_path="/a/x")
    assert r.status_code == 303 and r.headers["location"] == "/a/x"
    assert "ad_session" in r.cookies or app_client.cookies.get("ad_session")
    me = app_client.get("/api/me").json()
    assert me["id"] == "ana@empresa.com" and me["bigquery_ok"] and not me["admin"]
    assert app_client.post("/auth/logout").json() == {"ok": True}
    assert app_client.get("/api/me").status_code == 401


def test_login_asks_consent_again_when_google_sends_no_refresh_token(app_client):
    r = _login(app_client, code="sin-refresh")
    assert r.headers["location"].startswith("/auth/google?consent=1")
    r = app_client.get(r.headers["location"], follow_redirects=False)
    query = parse_qs(urlsplit(r.headers["location"]).query)
    assert "consent" in query["prompt"][0]
    r = app_client.get("/auth/callback", params={"code": "sin-refresh", "state": query["state"][0]}, follow_redirects=False)
    assert r.headers["location"].startswith("/login?error=consent")


def test_login_rejections(app_client):
    app_client.claims["hd"] = None  # mail del dominio pero no del Workspace
    assert _login(app_client).headers["location"].startswith("/login?error=denied")
    r = app_client.get("/auth/callback", params={"code": "x", "state": "falso"}, follow_redirects=False)
    assert r.headers["location"].startswith("/login?error=expired")
    r = app_client.get("/auth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"].startswith("/login?error=cancelled")


def test_next_cannot_leave_the_site(app_client):
    r = _login(app_client, next_path="//malo.com/x")
    assert r.headers["location"] == "/"


def test_cross_origin_writes_are_refused(app_client):
    _login(app_client)
    assert app_client.post("/api/algo", headers={"Origin": "https://malo.com"}).status_code == 403
    assert app_client.post("/api/algo", headers={"Origin": BASE}).json() == {"ok": "ana@empresa.com"}


def test_projects_shares_and_moving_dashboards_over_http(app_client):
    _login(app_client)
    pid = app_client.post("/api/projects", json={"name": "Ventas"}).json()["id"]
    assert app_client.post(f"/api/projects/{pid}/members", json={"email": "beto@empresa.com", "role": "viewer"}).status_code == 200
    assert app_client.post(f"/api/projects/{pid}/members", json={"email": "nadie@gmail.com", "role": "viewer"}).status_code == 403
    share = app_client.post("/api/chats/c1/share", json={"project_id": pid}).json()
    assert app_client.get(f"/api/shares/{share['id']}").json()["items"][0]["text"] == "hola"
    assert app_client.post("/api/chats/otro/share", json={"project_id": pid}).status_code == 404
    aid = app_client.dash.store.create("Tablero", "ana@empresa.com")
    assert app_client.post(f"/api/artifacts/{aid}/project", json={"project_id": pid}).json()["project_id"] == pid
    assert app_client.get(f"/api/projects/{pid}").json()["shares"][0]["id"] == share["id"]
    # Beto entra: ve el proyecto y lo compartido, pero no administra.
    app_client.ctx.store.add_user("beto@empresa.com")
    app_client.cookies.clear()
    app_client.cookies.set("ad_session", app_client.ctx.store.create_session("beto@empresa.com", 1))
    assert [p["id"] for p in app_client.get("/api/me").json()["projects"]] == [pid]
    assert app_client.get(f"/api/shares/{share['id']}").status_code == 200
    assert app_client.delete(f"/api/shares/{share['id']}").status_code == 403
    assert app_client.patch(f"/api/projects/{pid}", json={"name": "x"}).status_code == 403
    assert app_client.post(f"/api/artifacts/{aid}/project", json={"project_id": None}).status_code == 403


def test_admin_users(app_client):
    app_client.claims.update(email="jefa@empresa.com", sub="222")
    _login(app_client)
    assert app_client.get("/api/me").json()["admin"]
    assert app_client.post("/api/admin/users", json={"email": "Nuevo@Gmail.com"}).json()["id"] == "nuevo@gmail.com"
    assert app_client.patch("/api/admin/users/jefa@empresa.com", json={"status": "disabled"}).status_code == 400
    token = app_client.ctx.store.create_session("nuevo@gmail.com", 1)
    app_client.patch("/api/admin/users/nuevo@gmail.com", json={"status": "disabled"})
    assert app_client.ctx.store.session_user(token) is None  # se le cerraron las sesiones


# --- limpieza --------------------------------------------------------------------------------------


def test_retention_deletes_old_chats_but_not_running_ones(store, monkeypatch):
    monkeypatch.setattr(config, "CHAT_RETENTION_DAYS", 30)
    old = time.time() - 40 * 86400
    sessions = [SimpleNamespace(id="viejo", user_id="ana", last_update_time=old),
                SimpleNamespace(id="corriendo", user_id="ana", last_update_time=old),
                SimpleNamespace(id="nuevo", user_id="ana", last_update_time=time.time())]
    deleted = []

    class FakeSessions:
        async def list_sessions(self, app_name, user_id=None):
            return SimpleNamespace(sessions=sessions)

        async def delete_session(self, app_name, user_id, session_id):
            deleted.append(session_id)

    out = asyncio.run(retention.sweep(FakeSessions(), store, lambda: {"corriendo"}))
    assert deleted == ["viejo"] and out["chats"] == 1


# --- migración -------------------------------------------------------------------------------------


def test_migrate_moves_everything_to_the_new_id(tmp_path, monkeypatch):
    import sqlite3

    from agent_dashboards.auth import migrate

    monkeypatch.setattr(config, "SESSIONS_DB", tmp_path / "sessions.db")
    monkeypatch.setattr(config, "KNOWLEDGE_DB", tmp_path / "knowledge.db")
    monkeypatch.setattr(config, "ARTIFACTS_DIR", tmp_path / "artifacts")
    conn = sqlite3.connect(tmp_path / "sessions.db")
    conn.execute("CREATE TABLE sessions (user_id TEXT, id TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('viejo', 'c1')")
    conn.commit()
    conn.close()
    d = tmp_path / "artifacts" / "tablero-abc123"
    (d / "db" / "data" / "users" / "viejo").mkdir(parents=True)
    (d / "_platform.json").write_text(json.dumps({"id": "tablero-abc123", "owner": "viejo"}), encoding="utf-8")
    assert migrate.migrate("viejo", "Lucia@Empresa.com", dry=True)["chats"] == 1
    assert json.loads((d / "_platform.json").read_text())["owner"] == "viejo"  # dry-run no toca nada
    result = migrate.migrate("viejo", "Lucia@Empresa.com")
    assert result["chats"] == 1 and result["dashboards"] == 1 and result["corridas de lector"] == 1
    assert json.loads((d / "_platform.json").read_text())["owner"] == "lucia@empresa.com"
    assert (d / "db" / "data" / "users" / "lucia@empresa.com").exists()
    conn = sqlite3.connect(tmp_path / "sessions.db")
    assert conn.execute("SELECT user_id FROM sessions").fetchone()[0] == "lucia@empresa.com"
    conn.close()
