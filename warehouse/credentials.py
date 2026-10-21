"""Con qué credenciales corre cada consulta al warehouse.

Con QUERY_CREDENTIALS=reader, el agente y los dashboards en vivo consultan con el
token de Google de la persona: cada uno ve lo que su cuenta puede leer en BigQuery.
El servidor registra al arrancar de dónde salen esos tokens (`set_token_provider`).
Sin proveedor (tests, `adk web`) o con QUERY_CREDENTIALS=server, se usan las
credenciales del servidor. El catálogo y las sugerencias siempre usan las del servidor.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .. import config
from .mcp_client import BigQueryMcpClient, auth_headers
from .mcp_client import client as server_client

_provider: Callable[[str], Awaitable[str]] | None = None


def set_token_provider(provider: Callable[[str], Awaitable[str]] | None) -> None:
    global _provider
    _provider = provider


def per_user() -> bool:
    return config.QUERY_CREDENTIALS == "reader" and _provider is not None


def client_for(user: str | None) -> BigQueryMcpClient:
    if not per_user() or not user:
        return server_client
    provider = _provider
    return BigQueryMcpClient(token_provider=lambda: provider(user))  # type: ignore[misc]


async def headers_for(user: str | None) -> dict[str, str]:
    if not per_user() or not user:
        return auth_headers()
    return await client_for(user).headers()
