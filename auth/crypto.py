"""Clave secreta de la app: firma de cookies cortas y cifrado de los tokens de Google."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .. import config

_key: bytes | None = None


def secret_key() -> bytes:
    """`SECRET_KEY` del entorno, o una generada una vez y guardada en DATA_DIR/secret_key (0600)."""
    global _key
    if _key is not None:
        return _key
    if config.SECRET_KEY:
        _key = config.SECRET_KEY.encode()
        return _key
    path = Path(config.DATA_DIR) / "secret_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(48))
    _key = path.read_text().strip().encode()
    return _key


def _derive(purpose: str) -> bytes:
    return hmac.new(secret_key(), purpose.encode(), hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(data: dict[str, Any], max_age: int) -> str:
    """Valor firmado (no cifrado) que vence: para el estado del login de Google."""
    body = _b64(json.dumps({**data, "_exp": int(time.time()) + max_age}, separators=(",", ":")).encode())
    mac = _b64(hmac.new(_derive("cookie"), body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def unsign(value: str) -> dict[str, Any] | None:
    try:
        body, mac = value.split(".", 1)
    except (AttributeError, ValueError):
        return None
    expected = _b64(hmac.new(_derive("cookie"), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        data = json.loads(_unb64(body))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("_exp", 0) < time.time():
        return None
    data.pop("_exp", None)
    return data


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive("google-tokens")))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def reset_for_tests() -> None:
    global _key
    _key = None
