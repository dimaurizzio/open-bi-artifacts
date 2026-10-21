"""Login con Google (OpenID Connect, flujo de código con PKCE) y tokens de BigQuery por persona.

- El id token se verifica con google-auth: firma, audiencia, emisor y vencimiento,
  más el `nonce` de este login y `email_verified`.
- Con QUERY_CREDENTIALS=reader se pide además el permiso de BigQuery y acceso
  offline: el refresh token se guarda cifrado y `TokenBroker` saca tokens de acceso
  para que las consultas de cada persona corran con su propia cuenta.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import google.auth.transport.requests
import httpx
from google.oauth2 import id_token as google_id_token

from .. import config
from ..warehouse.mcp_client import WarehouseError
from . import crypto
from .store import AppStore

logger = logging.getLogger("agent_dashboards.auth")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class LoginError(Exception):
    """El login no se pudo completar. `public` es lo que se le muestra a la persona."""

    def __init__(self, public: str, detail: str = ""):
        super().__init__(detail or public)
        self.public = public


class NeedsGoogleConsent(WarehouseError):
    """La persona no dio (o revocó) el permiso de BigQuery: tiene que volver a entrar."""


def configured() -> bool:
    return bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET)


def reader_mode() -> bool:
    return config.QUERY_CREDENTIALS == "reader"


def scopes() -> list[str]:
    base = ["openid", "email", "profile"]
    return base + [config.GOOGLE_BIGQUERY_SCOPE] if reader_mode() else base


def new_flow() -> dict[str, str]:
    """state, nonce y verificador PKCE de un login nuevo."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24), "verifier": verifier, "challenge": challenge}


def authorization_url(redirect_uri: str, flow: dict[str, str], *, consent: bool, login_hint: str | None = None) -> str:
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes()),
        "state": flow["state"],
        "nonce": flow["nonce"],
        "code_challenge": flow["challenge"],
        "code_challenge_method": "S256",
        "include_granted_scopes": "true",
        # Sin consentimiento explícito Google no vuelve a mandar el refresh token.
        "prompt": "consent select_account" if consent else "select_account",
    }
    if reader_mode():
        params["access_type"] = "offline"
    if len(config.ALLOWED_EMAIL_DOMAINS) == 1 and not config.ALLOWED_EMAILS:
        params["hd"] = next(iter(config.ALLOWED_EMAIL_DOMAINS))  # sugiere la cuenta del dominio
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


async def _post_token(data: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(TOKEN_URL, data=data)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        raise LoginError("Google rechazó el login. Probá de nuevo.", f"token endpoint {r.status_code}: {body or r.text[:200]}")
    return body


async def exchange_code(code: str, redirect_uri: str, verifier: str) -> dict[str, Any]:
    return await _post_token({
        "code": code,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    })


# Tolerancia con el reloj de la máquina: unos segundos de atraso hacen que Google
# emita el token "en el futuro" ("Token used too early") y el login falle.
_CLOCK_SKEW_SECONDS = 60


def _verify(token: str) -> dict[str, Any]:
    return google_id_token.verify_oauth2_token(
        token,
        google.auth.transport.requests.Request(),
        config.GOOGLE_CLIENT_ID,
        clock_skew_in_seconds=_CLOCK_SKEW_SECONDS,
    )


async def verify_id_token(token: str, nonce: str) -> dict[str, Any]:
    try:
        claims = await asyncio.to_thread(_verify, token)
    except ValueError as e:
        raise LoginError("No se pudo verificar la cuenta de Google.", str(e)) from e
    if claims.get("iss") not in ISSUERS:
        raise LoginError("No se pudo verificar la cuenta de Google.", f"issuer {claims.get('iss')}")
    if claims.get("nonce") != nonce:
        raise LoginError("El login venció o se mezcló con otro. Probá de nuevo.", "nonce distinto")
    if not claims.get("email") or not claims.get("email_verified"):
        raise LoginError("La cuenta de Google no tiene un mail verificado.")
    return claims


def granted_bigquery(token_response: dict[str, Any]) -> bool:
    return config.GOOGLE_BIGQUERY_SCOPE in str(token_response.get("scope", "")).split()


class TokenBroker:
    """Tokens de acceso de Google por persona, a partir del refresh token guardado."""

    def __init__(self, store: AppStore):
        self.store = store
        self._cache: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def save(self, user: str, token_response: dict[str, Any]) -> None:
        """Después del login: guarda el refresh token (si vino) y el token de acceso en memoria."""
        if token_response.get("refresh_token"):
            self.store.put_google_token(user, crypto.encrypt(token_response["refresh_token"]), token_response.get("scope", ""))
        if token_response.get("access_token") and granted_bigquery(token_response):
            self._cache[user] = (token_response["access_token"], time.time() + int(token_response.get("expires_in", 3600)))

    def has_bigquery(self, user: str) -> bool:
        row = self.store.google_token(user)
        return bool(row) and config.GOOGLE_BIGQUERY_SCOPE in row["scopes"].split()

    def forget(self, user: str) -> None:
        self._cache.pop(user, None)

    async def access_token(self, user: str) -> str:
        cached = self._cache.get(user)
        if cached and cached[1] - 60 > time.time():
            return cached[0]
        async with self._locks.setdefault(user, asyncio.Lock()):
            cached = self._cache.get(user)
            if cached and cached[1] - 60 > time.time():
                return cached[0]
            row = self.store.google_token(user)
            refresh = crypto.decrypt(row["refresh_token"]) if row else None
            if not refresh or config.GOOGLE_BIGQUERY_SCOPE not in row["scopes"].split():
                raise NeedsGoogleConsent("Falta tu permiso de BigQuery: salí y volvé a entrar para darlo.")
            try:
                body = await _post_token({
                    "client_id": config.GOOGLE_CLIENT_ID,
                    "client_secret": config.GOOGLE_CLIENT_SECRET,
                    "refresh_token": refresh,
                    "grant_type": "refresh_token",
                })
            except LoginError as e:
                if "invalid_grant" in str(e):  # revocado o vencido: hay que volver a pedirlo
                    self.store.delete_google_token(user)
                    raise NeedsGoogleConsent("Tu permiso de BigQuery venció o se revocó: salí y volvé a entrar.") from e
                raise WarehouseError("No se pudo renovar tu acceso a BigQuery. Probá en un momento.") from e
            self._cache[user] = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
            return body["access_token"]
